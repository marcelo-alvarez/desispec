from astropy.io import fits
import fitsio
from fitsio import FITS,FITSHDR

import numpy as np
import specex as spx

def write_psf(pyps,opts):    
    
    # initialize table writing
    spx.tablewrite_init(pyps)

    # get traces
    xtrace=spx.get_trace(pyps,'x')
    ytrace=spx.get_trace(pyps,'y')

    xtrace = np.reshape(xtrace,(pyps.nfibers,pyps.trace_ncoeff))
    ytrace = np.reshape(ytrace,(pyps.nfibers,pyps.trace_ncoeff))
    
    # get table
    table_col0 = spx.VectorString()
    table_col1 = spx.VectorDouble()
    table_col2 = spx.VectorInt()
    table_col3 = spx.VectorInt()
    
    table_bundle_id       = spx.VectorInt()
    table_bundle_ndata    = spx.VectorInt()
    table_bundle_nparams  = spx.VectorInt()
    table_bundle_chi2pdf  = spx.VectorDouble()
    
    spx.get_table(pyps,table_col0,table_col1,table_col2,table_col3,
                  table_bundle_id,table_bundle_ndata,table_bundle_nparams,
                  table_bundle_chi2pdf)
    
    # copy columns to numpy arrays 
    col0 = table_col0
    col1 = np.zeros((pyps.table_nrows,pyps.nfibers,pyps.ncoeff))
    col2 = np.zeros( pyps.table_nrows)
    col3 = np.zeros( pyps.table_nrows)

    i = 0
    for r in np.arange(pyps.table_nrows):
        for t2 in np.arange(pyps.nfibers):
            for t1 in np.arange(pyps.ncoeff):
                col1[r,t2,t1] = table_col1[i]
                i += 1
        col2[r] = table_col2[r]
        col3[r] = table_col3[r]

    # load table into data array for writing
    data = np.zeros(pyps.table_nrows,
                    dtype=[('PARAM',   'U8'),
                           ('COEFF',   'f8', (pyps.nfibers,pyps.ncoeff)),
                           ('LEGDEGX', 'i4'),
                           ('LEGDEGW', 'i4')]
    )
    
    data['PARAM']   = col0 
    data['COEFF']   = col1
    data['LEGDEGX'] = col2
    data['LEGDEGW'] = col3

    # pad PARAM strings left justified with spaces to 8 characters
    i=0
    for param in data['PARAM']:
        data['PARAM'][i] = param.ljust(8,' ')
        i += 1
        
    # open fitsfile
    fitsfile = FITS('fio.fits','rw',clobber=True)

    # write xtrace
    fitsfile.write(xtrace)
    
    fitsfile[0].write_key('EXTNAME',  'XTRACE','')
    fitsfile[0].write_key('FIBERMIN', pyps.FIBERMIN)
    fitsfile[0].write_key('FIBERMAX', pyps.FIBERMAX)
    fitsfile[0].write_key('WAVEMIN',  pyps.trace_WAVEMIN)
    fitsfile[0].write_key('WAVEMAX',  pyps.trace_WAVEMAX)
    fitsfile[0].write_key('PSFTYPE',  'GAUSS-HERMITE')
    fitsfile[0].write_key('PSFVER',   3)
    
    fitsfile[0].write_comment('PSF generated by specex, https://github.com/desihub/specex')

    # write ytrace
    fitsfile.write(ytrace)

    fitsfile[1].write_key('PCOUNT',   0,'required keyword; must = 0')
    fitsfile[1].write_key('GCOUNT',   1,'required keyword; must = 1')
    fitsfile[1].write_key('EXTNAME',  'YTRACE')
    fitsfile[1].write_key('FIBERMIN', pyps.FIBERMIN)
    fitsfile[1].write_key('FIBERMAX', pyps.FIBERMAX)
    fitsfile[1].write_key('WAVEMIN',  pyps.trace_WAVEMIN)
    fitsfile[1].write_key('WAVEMAX',  pyps.trace_WAVEMAX)

    # write table
    fitsfile.write(data)

    # write comments
    fitsfile[2].write_comment('------------------------------------------------------------------------')
    fitsfile[2].write_comment('PSF generated by specex, https://github.com/julienguy/specex')
    fitsfile[2].write_comment('PSF fit date 2020-11-12') # HARDCODED FOR DEV
    fitsfile[2].write_comment('-')
    fitsfile[2].write_comment('Each row of the table contains the data vector of one PSF parameter')
    fitsfile[2].write_comment('The size of the vector is ((FIBERMAX-FIBERMIN+1)*(LEGDEG+1))')
    fitsfile[2].write_comment('Description of  the NPARAMS parameters :')
    fitsfile[2].write_comment('GHSIGX   : Sigma of first Gaussian along CCD columns for PSF core')
    fitsfile[2].write_comment('GHSIGY   : Sigma of first Gaussian along CCD rows for PSF core')
    fitsfile[2].write_comment('GH-i-j   : Hermite pol. coefficents, i along columns, j along rows,')
    fitsfile[2].write_comment('         i is integer from 0 to GHDEGX, j is integer from 0 to GHDEGY,')
    fitsfile[2].write_comment('         there are (GHDEGX+1)*(GHDEGY+1) such coefficents.')
    fitsfile[2].write_comment('TAILAMP  : Amplitude of PSF tail')
    fitsfile[2].write_comment('TAILCORE : Size in pixels of PSF tail saturation in PSF core')
    fitsfile[2].write_comment('TAILXSCA : Scaling apply to CCD coordinate along columns for PSF tail')
    fitsfile[2].write_comment('TAILYSCA : Scaling apply to CCD coordinate along rows for PSF tail')
    fitsfile[2].write_comment('TAILINDE : Asymptotic power law index of PSF tail')
    fitsfile[2].write_comment('CONT     : Continuum flux in arc image (not part of PSF)')
    fitsfile[2].write_comment('-  ')
    fitsfile[2].write_comment('PSF_core(X,Y) = [ SUM_ij (GH-i-j)*HERM(i,X/GHSIGX)*HERM(j,Y/GHSIGX) ]')
    fitsfile[2].write_comment('                                       *GAUS(X,GHSIGX)*GAUS(Y,GHSIGY)')
    fitsfile[2].write_comment('-  ')
    fitsfile[2].write_comment('PSF_tail(X,Y) = TAILAMP*R^2/(TAILCORE^2+R^2)^(1+TAILINDE/2)')
    fitsfile[2].write_comment('                with R^2=(X/TAILXSCA)^2+(Y/TAILYSCA)^2')
    fitsfile[2].write_comment('-  ')
    fitsfile[2].write_comment('PSF_core is integrated in pixel')
    fitsfile[2].write_comment('PSF_tail is not, it is evaluated at center of pixel')
    fitsfile[2].write_comment('------------------------------------------------------------------------')

    # write keys
    fitsfile[2].write_key('EXTNAME','PSF','');    
    fitsfile[2].write_key('PSFTYPE','GAUSS-HERMITE','');
    fitsfile[2].write_key('PSFVER','3','');
    
    fitsfile[2].write_key('MJD',pyps.mjd,'MJD of arc lamp exposure');
    fitsfile[2].write_key('PLATEID',pyps.plate_id,'plate ID of arc lamp exposure');
    fitsfile[2].write_key('CAMERA',pyps.camera_id,'camera ID');
    fitsfile[2].write_key('ARCEXP',pyps.arc_exposure_id,'ID of arc lamp exposure used to fit PSF');
    
    fitsfile[2].write_key('NPIX_X',pyps.NPIX_X,'number of columns in input CCD image');
    fitsfile[2].write_key('NPIX_Y',pyps.NPIX_Y,'number of rows in input CCD image');
    fitsfile[2].write_key('HSIZEX',pyps.hSizeX,'Half size of PSF in fit, NX=2*HSIZEX+1');
    fitsfile[2].write_key('HSIZEY',pyps.hSizeY,'Half size of PSF in fit, NY=2*HSIZEY+1');
    fitsfile[2].write_key('FIBERMIN',pyps.FIBERMIN,'first fiber (starting at 0)');
    fitsfile[2].write_key('FIBERMAX',pyps.FIBERMAX,'last fiber (included)');
    fitsfile[2].write_key('NPARAMS',pyps.nparams_all,'number of PSF parameters');
    fitsfile[2].write_key('LEGDEG',(pyps.ncoeff-1),'degree of Legendre pol.(wave) for parameters');
    fitsfile[2].write_key('GHDEGX',pyps.GHDEGX,'degree of Hermite polynomial along CCD columns');
    fitsfile[2].write_key('GHDEGY',pyps.GHDEGY,'degree of Hermite polynomial along CCD rows');
    fitsfile[2].write_key('WAVEMIN',pyps.table_WAVEMIN,'minimum wavelength (A), used for the Legendre polynomials');
    fitsfile[2].write_key('WAVEMAX',pyps.table_WAVEMAX,'maximum wavelength (A), used for the Legendre polynomials');    
    fitsfile[2].write_key('PSFERROR',pyps.psf_error,'assumed PSF fractional error in chi2');
    fitsfile[2].write_key('READNOIS',pyps.readout_noise,'assumed read out noise in chi2');
    fitsfile[2].write_key('GAIN',pyps.gain,'assumed gain in chi2');

    i=0
    for bid in table_bundle_id:
        ndata   = table_bundle_ndata[i]
        nparams = table_bundle_nparams[i]
        chi2pdf = table_bundle_chi2pdf[i]
        i += 1

        keybase = 'B'+str(bid).rjust(2,'0')
        print(keybase)
        # chi2
        fitsfile[2].write_key(
            keybase+'RCHI2',chi2pdf,'best fit chi2/ndf for fiber bundle '
            +str(bid))
        # ndata
        fitsfile[2].write_key(
            keybase+'NDATA',ndata,'number of pixels in fit for fiber bundle '
            +str(bid))
        # chi2
        fitsfile[2].write_key(
            keybase+'NPAR ',nparams,'number of parameters in fit for fiber bundle '
            +str(bid))
                
    # close fits file
    fitsfile.close()

    return 
