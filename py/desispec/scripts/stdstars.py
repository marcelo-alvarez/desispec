

"""
Get the normalized best template to do flux calibration.
"""

#- TODO: refactor algorithmic code into a separate module/function

import argparse
import sys

import numpy as np
from astropy.io import fits
from astropy import units
from astropy.table import Table

from desispec import io
from desispec.fluxcalibration import match_templates,normalize_templates,isStdStar
from desispec.interpolation import resample_flux
from desiutil.log import get_logger
from desispec.parallel import default_nproc
from desispec.io.filters import load_legacy_survey_filter
from desiutil.dust import ext_odonnell
from desispec.fiberbitmasking import get_fiberbitmasked_frame

def parse(options=None):
    parser = argparse.ArgumentParser(description="Fit of standard star spectra in frames.")
    parser.add_argument('--frames', type = str, default = None, required=True, nargs='*',
                        help = 'list of path to DESI frame fits files (needs to be same exposure, spectro)')
    parser.add_argument('--skymodels', type = str, default = None, required=True, nargs='*',
                        help = 'list of path to DESI sky model fits files (needs to be same exposure, spectro)')
    parser.add_argument('--fiberflats', type = str, default = None, required=True, nargs='*',
                        help = 'list of path to DESI fiberflats fits files (needs to be same exposure, spectro)')
    parser.add_argument('--starmodels', type = str, help = 'path of spectro-photometric stellar spectra fits')
    parser.add_argument('-o','--outfile', type = str, help = 'output file for normalized stdstar model flux')
    parser.add_argument('--ncpu', type = int, default = default_nproc, required = False, help = 'use ncpu for multiprocessing')
    parser.add_argument('--delta-color', type = float, default = 0.2, required = False, help = 'max delta-color for the selection of standard stars (on top of meas. errors)')
    parser.add_argument('--color', type = str, default = "G-R", choices=['G-R', 'R-Z'], required = False, help = 'color for selection of standard stars')
    parser.add_argument('--z-max', type = float, default = 0.008, required = False, help = 'max peculiar velocity (blue/red)shift range')
    parser.add_argument('--z-res', type = float, default = 0.00002, required = False, help = 'dz grid resolution')
    parser.add_argument('--template-error', type = float, default = 0.1, required = False, help = 'fractional template error used in chi2 computation (about 0.1 for BOSS b1)')
    parser.add_argument('--maxstdstars', type=int, default=30, \
            help='Maximum number of stdstars to include')
    
    log = get_logger()
    args = None
    if options is None:
        args = parser.parse_args()
        cmd = ' '.join(sys.argv)
    else:
        args = parser.parse_args(options)
        cmd = 'desi_fit_stdstars ' + ' '.join(options)

    log.info('RUNNING {}'.format(cmd))

    return args

def safe_read_key(header,key) :
    value = None
    try :
        value=header[key]
    except KeyError :
        value = None
        pass
    if value is None : # second try
        value=header[key.ljust(8).upper()]
    return value

def dust_transmission(wave,ebv) :
    Rv = 3.1
    extinction = ext_odonnell(wave,Rv=Rv)
    return 10**(-Rv*extinction*ebv/2.5)

def main(args) :
    """ finds the best models of all standard stars in the frame
    and normlize the model flux. Output is written to a file and will be called for calibration.
    """

    log = get_logger()

    log.info("mag delta %s = %f (for the pre-selection of stellar models)"%(args.color,args.delta_color))
    log.info('multiprocess parallelizing with {} processes'.format(args.ncpu))

    # READ DATA
    ############################################
    # First loop through and group by exposure and spectrograph
    frames_by_expid = {}
    for filename in args.frames :
        log.info("reading %s"%filename)
        frame=io.read_frame(filename)
        expid = safe_read_key(frame.meta,"EXPID")
        camera = safe_read_key(frame.meta,"CAMERA").strip().lower()
        spec = camera[1]
        uniq_key = (expid,spec)
        if uniq_key in frames_by_expid.keys():
            frames_by_expid[uniq_key][camera] = frame
        else:
            frames_by_expid[uniq_key] = {camera: frame}

    frames={}
    flats={}
    skies={}

    spectrograph=None
    starfibers=None
    starindices=None
    fibermap=None

    # For each unique expid,spec pair, get the logical OR of the FIBERSTATUS for all
    # cameras and then proceed with extracting the frame information
    # once we modify the fibermap FIBERSTATUS
    for (expid,spec),camdict in frames_by_expid.items():

        fiberstatus = None
        for frame in camdict.values():
            if fiberstatus is None:
                fiberstatus = frame.fibermap['FIBERSTATUS'].data.copy()
            else:
                fiberstatus |= frame.fibermap['FIBERSTATUS']
            
        for camera,frame in camdict.items():
            frame.fibermap['FIBERSTATUS'] |= fiberstatus
            # Set fibermask flagged spectra to have 0 flux and variance                       
            frame = get_fiberbitmasked_frame(frame,bitmask='stdstars',ivar_framemask=True)
            frame_fibermap = frame.fibermap
            frame_starindices = np.where(isStdStar(frame_fibermap))[0]

            #- Confirm that all fluxes have entries but trust targeting bits
            #- to get basic magnitude range correct
            keep = np.ones(len(frame_starindices), dtype=bool)

            for colname in ['FLUX_G', 'FLUX_R', 'FLUX_Z']:  #- and W1 and W2?
                keep &= frame_fibermap[colname][frame_starindices] > 10**((22.5-30)/2.5)
                keep &= frame_fibermap[colname][frame_starindices] < 10**((22.5-0)/2.5)

            frame_starindices = frame_starindices[keep]
        
            if spectrograph is None :
                spectrograph = frame.spectrograph
                fibermap = frame_fibermap
                starindices=frame_starindices
                starfibers=fibermap["FIBER"][starindices]

            elif spectrograph != frame.spectrograph :
                log.error("incompatible spectrographs %d != %d"%(spectrograph,frame.spectrograph))
                raise ValueError("incompatible spectrographs %d != %d"%(spectrograph,frame.spectrograph))
            elif starindices.size != frame_starindices.size or np.sum(starindices!=frame_starindices)>0 :
                log.error("incompatible fibermap")
                raise ValueError("incompatible fibermap")

            if not camera in frames :
                frames[camera]=[]

            frames[camera].append(frame)

    # possibly cleanup memory
    del frames_by_expid

    for filename in args.skymodels :
        log.info("reading %s"%filename)
        sky=io.read_sky(filename)
        camera=safe_read_key(sky.header,"CAMERA").strip().lower()
        if not camera in skies :
            skies[camera]=[]
        skies[camera].append(sky)
        
    for filename in args.fiberflats :
        log.info("reading %s"%filename)
        flat=io.read_fiberflat(filename)
        camera=safe_read_key(flat.header,"CAMERA").strip().lower()

        # NEED TO ADD MORE CHECKS
        if camera in flats:
            log.warning("cannot handle several flats of same camera (%s), will use only the first one"%camera)
            #raise ValueError("cannot handle several flats of same camera (%s)"%camera)
        else :
            flats[camera]=flat
    

    if starindices.size == 0 :
        log.error("no STD star found in fibermap")
        raise ValueError("no STD star found in fibermap")

    log.info("found %d STD stars"%starindices.size)

    log.warning("Not using flux errors for Standard Star fits!")
    
    # DIVIDE FLAT AND SUBTRACT SKY , TRIM DATA
    ############################################
    # since poping dict, we need to copy keys to iterate over to avoid
    # RuntimeError due to changing dict
    frame_cams = list(frames.keys())
    for cam in frame_cams:

        if not cam in skies:
            log.warning("Missing sky for %s"%cam)
            frames.pop(cam)
            continue
        if not cam in flats:
            log.warning("Missing flat for %s"%cam)
            frames.pop(cam)
            continue
        
        flat=flats[cam]
        for frame,sky in zip(frames[cam],skies[cam]) :
            frame.flux = frame.flux[starindices]
            frame.ivar = frame.ivar[starindices]
            frame.ivar *= (frame.mask[starindices] == 0)
            frame.ivar *= (sky.ivar[starindices] != 0)
            frame.ivar *= (sky.mask[starindices] == 0)
            frame.ivar *= (flat.ivar[starindices] != 0)
            frame.ivar *= (flat.mask[starindices] == 0)
            frame.flux *= ( frame.ivar > 0) # just for clean plots
            for star in range(frame.flux.shape[0]) :
                ok=np.where((frame.ivar[star]>0)&(flat.fiberflat[star]!=0))[0]
                if ok.size > 0 :
                    frame.flux[star] = frame.flux[star]/flat.fiberflat[star] - sky.flux[star]
            frame.resolution_data = frame.resolution_data[starindices]

    # CHECK S/N
    ############################################
    # for each band in 'brz', record quadratic sum of median S/N across wavelength
    snr=dict()
    for band in ['b','r','z'] :
        snr[band]=np.zeros(starindices.size)
    for cam in frames :
        band=cam[0].lower()
        for frame in frames[cam] :
            msnr = np.median( frame.flux * np.sqrt( frame.ivar ) / np.sqrt(np.gradient(frame.wave)) , axis=1 ) # median SNR per sqrt(A.)
            msnr *= (msnr>0)
            snr[band] = np.sqrt( snr[band]**2 + msnr**2 )
    log.info("SNR(B) = {}".format(snr['b']))
    
    ###############################
    max_number_of_stars = 50
    min_blue_snr = 4.
    ###############################
    indices=np.argsort(snr['b'])[::-1][:max_number_of_stars]
    
    validstars = np.where(snr['b'][indices]>min_blue_snr)[0]
    
    #- TODO: later we filter on models based upon color, thus throwing
    #- away very blue stars for which we don't have good models.

    log.info("Number of stars with median stacked blue S/N > {} /sqrt(A) = {}".format(min_blue_snr,validstars.size))
    if validstars.size == 0 :
        log.error("No valid star")
        sys.exit(12)

    validstars = indices[validstars]

    for band in ['b','r','z'] :
        snr[band]=snr[band][validstars]
    
    log.info("BLUE SNR of selected stars={}".format(snr['b']))
    
    for cam in frames :
        for frame in frames[cam] :
            frame.flux = frame.flux[validstars]
            frame.ivar = frame.ivar[validstars]
            frame.resolution_data = frame.resolution_data[validstars]
    starindices = starindices[validstars]
    starfibers  = starfibers[validstars]
    nstars = starindices.size
    fibermap = Table(fibermap[starindices])

    # MASK OUT THROUGHPUT DIP REGION
    ############################################
    mask_throughput_dip_region = True
    if mask_throughput_dip_region :
        wmin=4300.
        wmax=4500.
        log.warning("Masking out the wavelength region [{},{}]A in the standard star fit".format(wmin,wmax))
    for cam in frames :
        for frame in frames[cam] :
            ii=np.where( (frame.wave>=wmin)&(frame.wave<=wmax) )[0]
            if ii.size>0 :
                frame.ivar[:,ii] = 0

    # READ MODELS
    ############################################
    log.info("reading star models in %s"%args.starmodels)
    stdwave,stdflux,templateid,teff,logg,feh=io.read_stdstar_templates(args.starmodels)

    # COMPUTE MAGS OF MODELS FOR EACH STD STAR MAG
    ############################################

    #- Support older fibermaps
    if 'PHOTSYS' not in fibermap.colnames:
        log.warning('Old fibermap format; using defaults for missing columns')
        log.warning("    PHOTSYS = 'S'")
        log.warning("    MW_TRANSMISSION_G/R/Z = 1.0")
        log.warning("    EBV = 0.0")
        fibermap['PHOTSYS'] = 'S'
        fibermap['MW_TRANSMISSION_G'] = 1.0
        fibermap['MW_TRANSMISSION_R'] = 1.0
        fibermap['MW_TRANSMISSION_Z'] = 1.0
        fibermap['EBV'] = 0.0

    model_filters = dict()
    for band in ["G","R","Z"] :
        for photsys in np.unique(fibermap['PHOTSYS']) :
            model_filters[band+photsys] = load_legacy_survey_filter(band=band,photsys=photsys)

    log.info("computing model mags for %s"%sorted(model_filters.keys()))
    model_mags = dict()
    fluxunits = 1e-17 * units.erg / units.s / units.cm**2 / units.Angstrom
    for filter_name, filter_response in model_filters.items():
        model_mags[filter_name] = filter_response.get_ab_magnitude(stdflux*fluxunits,stdwave)
    log.info("done computing model mags")

    # LOOP ON STARS TO FIND BEST MODEL
    ############################################
    linear_coefficients=np.zeros((nstars,stdflux.shape[0]))
    chi2dof=np.zeros((nstars))
    redshift=np.zeros((nstars))
    normflux=[]

    star_mags = dict()
    star_unextincted_mags = dict()
    for band in ['G', 'R', 'Z']:
        star_mags[band] = 22.5 - 2.5 * np.log10(fibermap['FLUX_'+band])
        star_unextincted_mags[band] = 22.5 - 2.5 * np.log10(fibermap['FLUX_'+band] / fibermap['MW_TRANSMISSION_'+band])

    star_colors = dict()
    star_colors['G-R'] = star_mags['G'] - star_mags['R']
    star_colors['R-Z'] = star_mags['R'] - star_mags['Z']

    star_unextincted_colors = dict()
    star_unextincted_colors['G-R'] = star_unextincted_mags['G'] - star_unextincted_mags['R']
    star_unextincted_colors['R-Z'] = star_unextincted_mags['R'] - star_unextincted_mags['Z']

    fitted_model_colors = np.zeros(nstars)

    for star in range(nstars) :

        log.info("finding best model for observed star #%d"%star)

        # np.array of wave,flux,ivar,resol
        wave = {}
        flux = {}
        ivar = {}
        resolution_data = {}
        for camera in frames :
            for i,frame in enumerate(frames[camera]) :
                identifier="%s-%d"%(camera,i)
                wave[identifier]=frame.wave
                flux[identifier]=frame.flux[star]
                ivar[identifier]=frame.ivar[star]
                resolution_data[identifier]=frame.resolution_data[star]

        # preselect models based on magnitudes
        photsys=fibermap['PHOTSYS'][star]
        if not args.color in ['G-R','R-Z'] :
            raise ValueError('Unknown color {}'.format(args.color))
        bands=args.color.split("-")
        model_colors = model_mags[bands[0]+photsys] - model_mags[bands[1]+photsys]
        
        color_diff = model_colors - star_unextincted_colors[args.color][star]
        selection = np.abs(color_diff) < args.delta_color
        if np.sum(selection) == 0 :
            log.warning("no model in the selected color range for this star")
            continue
        
        
        # smallest cube in parameter space including this selection (needed for interpolation)
        new_selection = (teff>=np.min(teff[selection]))&(teff<=np.max(teff[selection]))
        new_selection &= (logg>=np.min(logg[selection]))&(logg<=np.max(logg[selection]))
        new_selection &= (feh>=np.min(feh[selection]))&(feh<=np.max(feh[selection]))
        selection = np.where(new_selection)[0]

        log.info("star#%d fiber #%d, %s = %f, number of pre-selected models = %d/%d"%(
            star, starfibers[star], args.color, star_unextincted_colors[args.color][star],
            selection.size, stdflux.shape[0]))
        
        # Match unextincted standard stars to data
        coefficients, redshift[star], chi2dof[star] = match_templates(
            wave, flux, ivar, resolution_data,
            stdwave, stdflux[selection],
            teff[selection], logg[selection], feh[selection],
            ncpu=args.ncpu, z_max=args.z_max, z_res=args.z_res,
            template_error=args.template_error
            )
        
        linear_coefficients[star,selection] = coefficients
        
        log.info('Star Fiber: {}; TEFF: {:.3f}; LOGG: {:.3f}; FEH: {:.3f}; Redshift: {:g}; Chisq/dof: {:.3f}'.format(
            starfibers[star],
            np.inner(teff,linear_coefficients[star]),
            np.inner(logg,linear_coefficients[star]),
            np.inner(feh,linear_coefficients[star]),
            redshift[star],
            chi2dof[star])
            )
        
        # Apply redshift to original spectrum at full resolution
        model=np.zeros(stdwave.size)
        redshifted_stdwave = stdwave*(1+redshift[star])
        for i,c in enumerate(linear_coefficients[star]) :
            if c != 0 :
                model += c*np.interp(stdwave,redshifted_stdwave,stdflux[i])

        # Apply dust extinction to the model
        log.info("Applying MW dust extinction to star {} with EBV = {}".format(star,fibermap['EBV'][star]))
        model *= dust_transmission(stdwave, fibermap['EBV'][star])

        # Compute final color of dust-extincted model
        photsys=fibermap['PHOTSYS'][star]
        if not args.color in ['G-R','R-Z'] :
            raise ValueError('Unknown color {}'.format(args.color))
        bands=args.color.split("-")
        model_mag1 = model_filters[bands[0]+photsys].get_ab_magnitude(model*fluxunits, stdwave)
        model_mag2 = model_filters[bands[1]+photsys].get_ab_magnitude(model*fluxunits, stdwave)
        fitted_model_colors[star] = model_mag1 - model_mag2
        if bands[0]=="R" :
            model_magr = model_mag1 
        elif bands[1]=="R" :
            model_magr = model_mag2

        #- TODO: move this back into normalize_templates, at the cost of
        #- recalculating a model magnitude?

        # Normalize the best model using reported magnitude
        scalefac=10**((model_magr - star_mags['R'][star])/2.5)

        log.info('scaling R mag {:.3f} to {:.3f} using scale {}'.format(model_magr, star_mags['R'][star], scalefac))
        normflux.append(model*scalefac)

    # Now write the normalized flux for all best models to a file
    normflux=np.array(normflux)

    fitted_stars = np.where(chi2dof != 0)[0]
    if fitted_stars.size == 0 :
        log.error("No star has been fit.")
        sys.exit(12)

    data={}
    data['LOGG']=linear_coefficients[fitted_stars,:].dot(logg)
    data['TEFF']= linear_coefficients[fitted_stars,:].dot(teff)
    data['FEH']= linear_coefficients[fitted_stars,:].dot(feh)
    data['CHI2DOF']=chi2dof[fitted_stars]
    data['REDSHIFT']=redshift[fitted_stars]
    data['COEFF']=linear_coefficients[fitted_stars,:]
    data['DATA_%s'%args.color]=star_colors[args.color][fitted_stars]
    data['MODEL_%s'%args.color]=fitted_model_colors[fitted_stars]
    data['BLUE_SNR'] = snr['b'][fitted_stars]
    data['RED_SNR']  = snr['r'][fitted_stars]
    data['NIR_SNR']  = snr['z'][fitted_stars]
    io.write_stdstar_models(args.outfile,normflux,stdwave,starfibers[fitted_stars],data)

