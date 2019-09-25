# @auto-fold regex /^\s*if/ /^\s*else/ /^\s*elif/ /^\s*def/
"""Main driver of the fitting routine."""
import os
import pickle
import random
import time
import warnings
from contextlib import closing
from multiprocessing import Pool, cpu_count

import astropy.units as u
import scipy as sp
import scipy.stats as st
from tabulate import tabulate

import dynesty
import Star
from dynesty.utils import resample_equal
from Error import *
from isochrone import estimate
from phot_utils import *
from sed_library import *
from utils import *

try:
    from isochrones import SingleStarModel, get_ichrone
except ModuleNotFoundError:
    wrn = 'Isochrones package not found. Mass, Age, and log g estimation '
    wrn += 'unavaliable'
    warnings.warn(wrn)
try:
    import pymultinest
except ModuleNotFoundError:
    warnings.warn(
        '(py)MultiNest installation (or libmultinest.dylib) not detected.'
    )


class Fitter:

    def __init__(self):

        # Default values for attributes
        self.out_folder = None
        self.verbose = True
        self.star = None
        self.setup = ['dynesty']
        self.norm = False
        self.grid = 'phoenix'
        self.estimate_logg = False
        self.priorfile = None

    @property
    def star(self):
        """Star to fit for."""
        return self._star

    @star.setter
    def star(self, star):
        if not isinstance(star, Star) and star is not None:
            InstanceError(star, Star).raise_()
        self._star = star

    @property
    def setup(self):
        """Set-up options."""
        return self._setup

    @setup.setter
    def setup(self, setup):
        err_msg = 'The setup has to contain at least the fitting engine'
        err_msg += ', multinest or dynesty.'
        if len(setup) < 1:
            InputError(setup, err_msg).raise_()
        self._setup = setup
        self._engine = setup[0]
        defaults = False
        if len(setup) == 1:
            defaults = True
            print('USING DEFAULT SETUP VALUES.')
        if self._engine == 'multinest':
            if defaults:
                self._nlive = 500
                self._dlogz = 0.5
            else:
                self._nlive = setup[1]
                self._dlogz = setup[2]
        if self._engine == 'dynesty':
            if defaults:
                self._nlive = 500
                self._dlogz = 0.5
                self._bound = 'multi'
                self._sample = 'rwalk'
                self._threads = 1
                self._dynamic = False
            else:
                self._nlive = setup[1]
                self._dlogz = setup[2]
                self._bound = setup[3]
                self._sample = setup[4]
                self._threads = setup[5]
                self._dynamic = setup[6]

    @property
    def norm(self):
        """Bool to decide if a normalization constant will be fitted.

        Set as True to not fit for radius and distance and fit for a
        normalization constant. After the fit a radius is calculated using
        the Gaia parallax.
        """
        return self._norm

    @norm.setter
    def norm(self, norm):
        if type(norm) is not bool:
            rInputError(norm, 'norm must be True or False.').raise_()
        self._norm = norm

    @property
    def grid(self):
        """Model grid selected. For now only Phoenix v2 is available."""
        return self._grid

    @grid.setter
    def grid(self, grid):
        # TODO: change the interpolators name to the respective model grid.
        assert grid == 'phoenix', 'The only available grid is Phoenix v2.'
        self._grid = grid
        if grid == 'phoenix':
            with closing(open('interpolations.pkl', 'rb')) as intp:
                self._interpolators = pickle.load(intp)

    @property
    def bma(self):
        """Bayesian Model Averaging (BMA).

        Set to True if BMA is wanted. This loads every model grid interpolator
        and fits an SED to all of them, so the runtime will be slower!
        """
        return self._bma

    @bma.setter
    def bma(self, bma):
        # TODO: IMPLEMENT
        raise NotImplementedError()

    @property
    def estimate_logg(self):
        """Estimate log g.

        Set to True if log g estimation with MIST isochrones is needed.
        """
        return self._estimate_logg

    @estimate_logg.setter
    def estimate_logg(self, estimate_logg):
        err_msg = 'estimate_logg must be True or False.'
        if type(estimate_logg) is not bool:
            InputError(estimate_logg, err_msg).raise_()
        self._estimate_logg = estimate_logg

    @property
    def verbose(self):
        """Program verbosity. Default is True."""
        return self._verbose

    @verbose.setter
    def verbose(self, verbose):
        if type(verbose) is not bool:
            InputError(verbose, 'verbose must be True or False.').raise_()
        self._verbose = verbose

    @property
    def priorfile(self):
        """Priorfile location."""
        return self._priorfile

    @priorfile.setter
    def priorfile(self, priorfile):
        if type(priorfile) is not str and priorfile is not None:
            err_msg = 'Priorfile must be an address or None.'
            InputError(priorfile, err_msg).raise_()
        self._priorfile = priorfile

    @property
    def out_folder(self):
        """Output folder.

        If none is provided the default will be the starname.
        """
        return self._out_folder

    @out_folder.setter
    def out_folder(self, out_folder):
        if type(out_folder) is not str and out_folder is not None:
            err_msg = 'Output folder must be an address or None.'
            InputError(out_folder, err_msg).raise_()
        self._out_folder = out_folder

    def initialize(self):
        """Initialize the fitter.

        To be run only after every input is added.
        This function calculates the number of dimensions, runs the prior
        creation, creates output directory, initializes coordinators and sets
        up global variables.
        """
        global prior_dict, coordinator, fixed, order, interpolators, star
        global use_norm
        err_msg = 'No star is detected. Please create an instance of Star.'
        if self.star is None:
            InputError(self.star, err_msg).raise_()
        star = self.star
        interpolators = self._interpolators
        use_norm = self.norm

        # Declare order of parameters.
        if not self.norm:
            order = sp.array(
                [
                    'teff', 'logg', 'z',
                    'dist', 'rad', 'Av',
                    'inflation'
                ]
            )
        else:
            order = sp.array(['teff', 'logg', 'z', 'norm', 'Av', 'inflation'])

        # Create output directory
        if self.out_folder is None:
            self.out_folder = self.star.starname + '/'
        create_dir(self.out_folder)

        # Parameter coordination.
        # Order for the parameters are:
        # teff, logg, z, dist, rad, Av, inflation
        # or
        # teff, logg, z, norm, Av, inflation
        npars = 7 if not self.norm else 6
        self.coordinator = sp.zeros(npars)  # 1 for fixed params
        self.fixed = sp.zeros(npars)
        coordinator = self.coordinator
        fixed = self.fixed

        # Setup priors.
        self.default_priors = self._default_priors()
        self.create_priors(self.priorfile)
        prior_dict = self.priors

        # Get dimensions.
        self.ndim = self.get_ndim()

    def get_ndim(self):
        """Calculate number of dimensions."""
        ndim = 7 if not self.norm else 6
        ndim -= self.coordinator.sum()
        return int(ndim)

    def _default_priors(self):
        defaults = dict()
        # Logg prior setup.
        if not self.estimate_logg:
            with closing(open('../Datafiles/logg_ppf.pkl', 'rb')) as jar:
                defaults['logg'] = pickle.load(jar)
        else:
            params = dict()  # params for isochrones.
            if self.star.get_temp:
                params['Teff'] = (self.star.temp, self.star.temp_e)
            if self.star.get_lum and self.star.lum is not None:
                params['LogL'] = (sp.log10(self.star.lum),
                                  sp.log10(self.star.lum_e))
            if self.star.get_rad and self.star.rad is not None:
                params['radius'] = (self.star.rad, self.star.rad_e)
            if self.star.get_plx:
                params['parallax'] = (self.star.plx, self.star.plx_e)
            mask = sp.array([1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0])
            mags = self.star.mags[mask == 1]
            mags_e = self.star.mag_errs[mask == 1]
            bands = ['H', 'J', 'Ks', 'G', 'RP', 'BP', 'W1', 'W2']
            used_bands = []
            for m, e, b in zip(mags, mags_e, bands):
                if m != 0:
                    params[b] = (m, e)
                    used_bands.append(b)
            if self.verbose and self.estimate_logg:
                print('*** ESTIMATING LOGG USING MIST ISOCHRONES ***')
            logg_est = estimate(used_bands, params)
            if logg_est is not None:
                defaults['logg'] = st.norm(loc=logg_est[0], scale=logg_est[1])
        # Teff prior setup.
        if self.star.get_temp:
            defaults['teff'] = st.norm(
                loc=self.star.temp, scale=self.star.temp_e)
        else:
            with closing(open('../Datafiles/teff_ppf.pkl', 'rb')) as jar:
                defaults['teff'] = pickle.load(jar)
            defaults['teff'] = teff_prior['teff']
        defaults['z'] = st.norm(loc=-0.125, scale=0.234)
        if not self._norm:
            defaults['dist'] = st.norm(
                loc=self.star.dist, scale=self.star.dist_e)
            if self.star.rad is not None:
                defaults['rad'] = st.norm(
                    loc=self.star.rad, scale=self.star.rad_e)
            else:
                wrn_msg = 'No radius found in Gaia, using default radius prior'
                wrn_msg += '. Consider fitting the normalization constant'
                wrn_msg += ' instead.'
                warnings.warn(wrn_msg)
                defaults['rad'] = st.uniform(0.1, 10)
        else:
            up = 1 / 1e-20
            defaults['norm'] = st.truncnorm(a=0, b=up, loc=0, scale=1e-20)
        if self.star.Av == 0.:
            self.coordinator[-2] = 1
            self.fixed[-2] = 0
            defaults['Av'] = None
        else:
            defaults['Av'] = st.uniform(loc=0, scale=self.star.Av)
        up, low = (5 - 0.5) / 0.5, (0 - 0.5) / 0.5
        defaults['inflation'] = st.truncnorm(a=low, b=up, loc=0.5, scale=0.5)
        return defaults

    def create_priors(self, priorfile):
        """Read the prior file.

        Returns a dictionary with each parameter's prior
        """
        param_list = [
            'teff', 'logg', 'z',
            'dist', 'rad', 'norm',
            'Av', 'inflation'
        ]
        if priorfile:
            param, prior, bounds = sp.loadtxt(
                priorfile, usecols=[0, 1, 2], unpack=True, dtype=object)
            # Dict with priors.
            prior_dict = dict()
            for par, pri, bo in zip(param, prior, bounds):
                if par not in param_list:
                    PriorError(par, 0).raise_()
                if self.norm and (par == 'dist' or par == 'rad'):
                    PriorError(par, 1).raise_()
                if pri.lower() == 'uniform':
                    a, b = bo.split(',')
                    a, b = float(a), float(b)
                    prior_dict[par] = st.uniform(loc=a, scale=b - a)
                elif pri.lower() == 'normal':
                    mu, sig = bo.split(',')
                    mu, sig = float(mu), float(sig)
                    prior_dict[par] = st.norm(loc=mu, scale=sig)
                elif pri.lower() == 'truncatednormal':
                    mu, sig, up, low = bo.split(',')
                    mu, sig, up, low = float(mu), float(
                        sig), float(up), float(low)
                    up, low = (up - mu) / sig, (low - mu) / sig
                    priot_dict[par] = st.truncnorm(
                        a=low, b=up, loc=mu, scale=sig)
                elif pri.lower() == 'default':
                    prior_dict[par] = self.default_priors[par]
                elif pri.lower() == 'fixed':
                    idx = sp.where(par == order)[0]
                    self.coordinator[idx] = 1
                    self.fixed[idx] = float(bo)
            self.priors = prior_dict
        else:
            warnings.warn('No priorfile detected. Using default priors.')
            self.priors = self.default_priors
        pass

    def fit(self):
        """Run fitting routine."""
        self.start = time.time()
        if self._engine == 'multinest':
            self.fit_multinest()
        else:
            self.fit_dynesty()
        elapsed_time = execution_time(self.start)
        end(self.coordinator, elapsed_time,
            self.out_folder, self._engine, self.norm)
        pass

    def fit_multinest(self):
        """Run MuiltiNest."""
        display(self._engine, self.star, self._nlive, self._dlogz, self.ndim)
        path = self.out_folder + 'multinest/'
        create_dir(path)  # Create multinest path.
        pymultinest.run(
            multinest_log_like, pt_multinest, self.ndim,
            n_params=self.ndim,
            sampling_efficiency=0.8,
            evidence_tolerance=self._dlogz,
            n_live_points=self._nlive,
            outputfiles_basename=path + 'chains',
            max_modes=100,
            verbose=self.verbose,
            resume=False
        )
        self.save()
        pass

    def fit_dynesty(self):
        """Run dynesty."""
        display(self._engine, self.star, self._nlive,
                self._dlogz, self.ndim, self._bound, self._sample,
                self._threads, self._dynamic)
        if self._dynamic:
            if self._threads > 1:
                with closing(Pool(self._threads)) as executor:
                    sampler = dynesty.DynamicNestedSampler(
                        dynesty_log_like, pt_dynesty, self.ndim,
                        bound=self._bound, sample=self._sample, pool=executor,
                        queue_size=self._threads - 1
                    )
                    sampler.run_nested(dlogz_init=self._dlogz,
                                       nlive_init=self._nlive)
            else:
                sampler = dynesty.DynamicNestedSampler(
                    dynesty_log_like, pt_dynesty, self.ndim,
                    bound=self._bound, sample=self._sample

                )
                sampler.run_nested(dlogz_init=self._dlogz,
                                   nlive_init=self._nlive)
        else:
            if self._threads > 1:
                with closing(Pool(self._threads)) as executor:
                    sampler = dynesty.NestedSampler(
                        dynesty_log_like, pt_dynesty, self.ndim,
                        nlive=self._nlive, bound=self._bound,
                        sample=self._sample, pool=executor,
                        queue_size=self._threads - 1
                    )
                    sampler.run_nested(dlogz=self._dlogz)
            else:
                sampler = dynesty.NestedSampler(
                    dynesty_log_like, pt_dynesty, self.ndim,
                    nlive=self._nlive, bound=self._bound,
                    sample=self._sample
                )
                sampler.run_nested(dlogz=self._dlogz)
        results = sampler.results
        self.save(results=results)
        pass

    def save(self, results=None):
        """Save multinest/dynesty output and relevant information.

        Saves a dictionary as a pickle file. The dictionary contains the
        following:

        lnZ : The global evidence
        lnZerr : The global evidence error
        posterior_samples : A dictionary containing the samples of each
                            parameter (even if it's fixed) and the
                            log likelihood for each set of sampled parameters.
        fixed : An array with the fixed parameter values
        coordinator : An array with the status of each parameter (1 for fixed
                      0 for free)
        best_fit : The best fit is chosen to be the median of each sample.
                   It also includes the log likelihood of the best fit.
        star : The Star object containing the information of the star (name,
               magnitudes, fluxes, coordinates, etc)
        engine : The fitting engine used (i.e. MultiNest or Dynesty)

        Also creates a log file with the best fit parameters and 1 sigma
        error bars.

        """
        out = dict()
        logdat = 'Parameter\tmedian\tupper\tlower\n'
        out_file = self.out_folder + '/' + self._engine + '_out.pkl'
        log_out = self.out_folder + '/' + 'best_fit.dat'
        if self._engine == 'multinest':
            lnz, lnzer, posterior_samples = self.multinest_results()
        else:
            lnz, lnzer, posterior_samples = self.dynesty_results(results)

        out['lnZ'] = lnz
        out['lnZerr'] = lnzer

        out['posterior_samples'] = dict()
        j = 0
        for i, param in enumerate(order):
            if not self.coordinator[i]:
                out['posterior_samples'][param] = posterior_samples[:, j]
                j += 1
            else:
                out['posterior_samples'][param] = self.fixed[i]
        out['posterior_samples']['loglike'] = sp.zeros(
            posterior_samples.shape[0])
        for i in range(posterior_samples.shape[0]):
            theta = build_params(
                posterior_samples[i, :], coordinator, fixed, self.norm)
            out['posterior_samples']['loglike'][i] = log_likelihood(
                theta, self.star, interpolators, self.norm)
        out['fixed'] = self.fixed
        out['coordinator'] = self.coordinator
        out['best_fit'] = dict()
        best_theta = sp.zeros(order.shape[0])
        j = 0
        for i, param in enumerate(order):
            if not self.coordinator[i]:
                out['posterior_samples'][param] = posterior_samples[:, j]
                out['best_fit'][param] = sp.median(posterior_samples[:, j])
                logdat += param + \
                    '\t{:.4f}\t'.format(sp.median(posterior_samples[:, j]))
                _, lo, up = credibility_interval(posterior_samples[:, j])
                logdat += '{:.4f}\t{:.4f}\n'.format(up, lo)
                j += 1
            else:
                out['best_fit'][param] = self.fixed[i]
                logdat += param + '\t{:.4f}\t'.format(self.fixed[i])
                logdat += 'FIXED\n'
            best_theta[i] = out['best_fit'][param]
        out['best_fit']['likelihood'] = log_likelihood(
            best_theta, self.star, interpolators, self.norm)
        out['star'] = self.star
        out['engine'] = self._engine
        out['norm'] = self.norm
        out['model_grid'] = self.grid
        with closing(open(log_out, 'w')) as logfile:
            logfile.write(logdat)
        pickle.dump(out, open(out_file, 'wb'))
        pass

    def multinest_results(self):
        """Extract posterior samples, global evidence and its error."""
        path = self.out_folder + 'multinest/'
        output = pymultinest.Analyzer(outputfiles_basename=path + 'chains',
                                      n_params=self.ndim)
        posterior_samples = output.get_equal_weighted_posterior()[:, :-1]
        lnz = output.get_stats()['global evidence']
        lnzer = output.get_stats()['global evidence error']
        return lnz, lnzer, posterior_samples

    def dynesty_results(self, results):
        """Extract posterior samples, global evidence and its error."""
        weights = sp.exp(results['logwt'] - results['logz'][-1])
        posterior_samples = resample_equal(results.samples, weights)
        lnz = results.logz[-1]
        lnzer = results.logzerr[-1]
        return lnz, lnzer, posterior_samples


#####################

def dynesty_log_like(cube):
    """Dynesty log likelihood wrapper."""
    theta = build_params(cube, coordinator, fixed, use_norm)
    return log_likelihood(theta, star, interpolators, use_norm)


def pt_dynesty(cube):
    """Dynesty prior transform."""
    return prior_transform_dynesty(cube, star, prior_dict,
                                   coordinator, use_norm)


def multinest_log_like(cube, ndim, nparams):
    """Multinest log likelihood wrapper."""
    theta = [cube[i] for i in range(ndim)]
    theta = build_params(theta, coordinator, fixed, use_norm)
    return log_likelihood(theta, star, interpolators, use_norm)


def pt_multinest(cube, ndim, nparams):
    """Multinest prior transform."""
    prior_transform_multinest(cube, star, prior_dict, coordinator, use_norm)


def log_prob(theta, star):
    """Wrap for sed_library.log_probability."""
    # DEPRECATED
    return log_probability(theta, star, prior_dict, coordinator, interpolators,
                           fixed)
