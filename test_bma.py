from astroARIADNE.fitter import Fitter
from astroARIADNE.plotter import SEDPlotter
from astroARIADNE.star import Star

if __name__ == '__main__':

    ra = 75.795
    dec = -30.399
    starname = 'NGTS-6'
    gaia_id = 4875693023844840448

    s = Star(starname, ra, dec, g_id=gaia_id)

    # Output setup
    out_folder = 'YOUR OUTPUT FOLDER HERE'
    in_file = out_folder + '/BMA.pkl'  # used as input for plotting
    plots_out_folder = out_folder + '/plots'

    # Setup parameters
    engine = 'dynesty'  # Only dynesty is available for BMA
    nlive = 100  # number of live points to use
    dlogz = 0.5  # evidence tolerance
    bound = 'multi'  # Unit cube bounds. Options are multi, single
    sample = 'rwalk'  # Sampling method. Options are rwalk, unif
    threads = 4  # Number of threads to use.
    dynamic = False  # Use dynamic nested sampling?
    setup = [engine, nlive, dlogz, bound, sample, threads, dynamic]
    models = [
        'phoenix',
        'btsettl',
        'btnextgen',
        'btcond',
        'kurucz',
        'ck04',
    ]

    # Now to setup the fitter and run the modelling.
    f = Fitter()
    f.star = s
    f.setup = setup
    f.norm = False  # fit normalization constant instead of radius + distance
    f.av_law = 'fitzpatrick'
    f.verbose = True
    f.out_folder = out_folder
    f.bma = True
    f.models = models
    f.n_samples = 100000  # If set as None it will choose automatically.
    f.prior_setup = {
        'teff': ('rave'),
        'logg': ('default'),
        'z': ('default'),
        'dist': ('default'),
        'rad': ('default'),
        'Av': ('default')
    }

    f.initialize()
    f.fit_bma()  # Begin fit!

    # Setting up plotter, which is independent to the main fitting routine
    # Bear in mind this will only work if you have downloaded the models
    # And have set up the ARIADNE_MODELS environment variable!
    artist = SEDPlotter(in_file, plots_out_folder, pdf=True)
    artist.plot_SED_no_model()  # Plots the stellar SED without the model
    artist.plot_SED()  # Plots stellar SED with model included
    artist.plot_bma_hist()  # Plots bayesian model averaging histograms
    artist.plot_bma_HR(10)  # Plots HR diagram with 10 samples from posterior
    artist.plot_corner()  # Corner plot of the posterior parameters