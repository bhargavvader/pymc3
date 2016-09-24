import shutil
import tempfile

import matplotlib
import numpy as np
import pandas as pd
import pymc3 as pm
import scipy.optimize as opt
import theano.tensor as tt

from .helpers import SeededTest

matplotlib.use('Agg', warn=False)


def get_city_data():
    """Helper to get city data"""
    data = pd.read_csv(pm.get_data_file('pymc3.examples', 'data/srrs2.dat'))
    cty_data = pd.read_csv(pm.get_data_file('pymc3.examples', 'data/cty.dat'))

    data = data[data.state == 'MN']

    data['fips'] = data.stfips * 1000 + data.cntyfips
    cty_data['fips'] = cty_data.stfips * 1000 + cty_data.ctfips
    data['lradon'] = np.log(np.where(data.activity == 0, .1, data.activity))
    data = data.merge(cty_data, 'inner', on='fips')

    unique = data[['fips']].drop_duplicates()
    unique['group'] = np.arange(len(unique))
    unique.set_index('fips')
    return data.merge(unique, 'inner', on='fips')


class ARM5_4(SeededTest):
    def build_model(self):
        wells = pm.get_data_file('pymc3.examples', 'data/wells.dat')
        data = pd.read_csv(wells, delimiter=u' ', index_col=u'id', dtype={u'switch': np.int8})
        data.dist /= 100
        data.educ /= 4
        col = data.columns
        P = data[col[1:]]
        P -= P.mean()
        P['1'] = 1

        with pm.Model() as model:
            effects = pm.Normal('effects', mu=0, tau=100. ** -2, shape=len(P.columns))
            p = pm.sigmoid(pm.dot(np.array(P), effects))
            pm.Bernoulli('s', p, observed=np.array(data.switch))
        return model

    def test_run(self):
        model = self.build_model()
        with model:
            # move the chain to the MAP which should be a good starting point
            start = pm.find_MAP()
            H = model.fastd2logp()  # find a good orientation using the hessian at the MAP
            h = H(start)

            step = pm.HamiltonianMC(model.vars, h)
            pm.sample(50, step, start)


class TestARM12_6(SeededTest):
    def build_model(self):
        data = get_city_data()

        self.obs_means = data.groupby('fips').lradon.mean().as_matrix()

        lradon = data.lradon.as_matrix()
        floor = data.floor.as_matrix()
        group = data.group.as_matrix()

        with pm.Model() as model:
            groupmean = pm.Normal('groupmean', 0, 10. ** -2.)
            groupsd = pm.Uniform('groupsd', 0, 10.)
            sd = pm.Uniform('sd', 0, 10.)
            floor_m = pm.Normal('floor_m', 0, 5. ** -2.)
            means = pm.Normal('means', groupmean, groupsd ** -2., shape=len(self.obs_means))
            pm.Normal('lr', floor * floor_m + means[group], sd ** -2., observed=lradon)
        return model

    def too_slow(self):
        model = self.build_model()
        start = {'groupmean': self.obs_means.mean(),
                 'groupsd_interval_': 0,
                 'sd_interval_': 0,
                 'means': self.obs_means,
                 'floor_m': 0.,
                 }
        with model:
            start = pm.find_MAP(start=start,
                                vars=[model['groupmean'], model['sd_interval_'], model['floor_m']])
            step = pm.NUTS(model.vars, scaling=start)
            pm.sample(50, step, start)


class TestARM12_6Uranium(SeededTest):
    def build_model(self):
        data = get_city_data()
        self.obs_means = data.groupby('fips').lradon.mean()

        lradon = data.lradon.as_matrix()
        floor = data.floor.as_matrix()
        group = data.group.as_matrix()
        ufull = data.Uppm.as_matrix()

        with pm.Model() as model:
            groupmean = pm.Normal('groupmean', 0, 10. ** -2.)
            groupsd = pm.Uniform('groupsd', 0, 10.)
            sd = pm.Uniform('sd', 0, 10.)
            floor_m = pm.Normal('floor_m', 0, 5. ** -2.)
            u_m = pm.Normal('u_m', 0, 5. ** -2)
            means = pm.Normal('means', groupmean, groupsd ** -2., shape=len(self.obs_means))
            pm.Normal('lr', floor * floor_m + means[group] + ufull * u_m, sd ** - 2.,
                      observed=lradon)
        return model

    def too_slow(self):
        model = self.build_model()
        with model:
            start = pm.Point({
                'groupmean': self.obs_means.mean(),
                'groupsd_interval_': 0,
                'sd_interval_': 0,
                'means': np.array(self.obs_means),
                'u_m': np.array([.72]),
                'floor_m': 0.,
            })

            start = pm.find_MAP(start, model.vars[:-1])
            H = model.fastd2logp()
            h = np.diag(H(start))

            step = pm.HamiltonianMC(model.vars, h)
            pm.sample(50, step, start)


def build_disaster_model(masked=False):
    disasters_data = np.array([4, 5, 4, 0, 1, 4, 3, 4, 0, 6, 3, 3, 4, 0, 2, 6,
                               3, 3, 5, 4, 5, 3, 1, 4, 4, 1, 5, 5, 3, 4, 2, 5,
                               2, 2, 3, 4, 2, 1, 3, 2, 2, 1, 1, 1, 1, 3, 0, 0,
                               1, 0, 1, 1, 0, 0, 3, 1, 0, 3, 2, 2, 0, 1, 1, 1,
                               0, 1, 0, 1, 0, 0, 0, 2, 1, 0, 0, 0, 1, 1, 0, 2,
                               3, 3, 1, 1, 2, 1, 1, 1, 1, 2, 4, 2, 0, 0, 1, 4,
                               0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1])
    if masked:
        disasters_data[[23, 68]] = -1
        disasters_data = np.ma.masked_values(disasters_data, value=-1)
    years = len(disasters_data)

    with pm.Model() as model:
        # Prior for distribution of switchpoint location
        switchpoint = pm.DiscreteUniform('switchpoint', lower=0, upper=years)
        # Priors for pre- and post-switch mean number of disasters
        early_mean = pm.Exponential('early_mean', lam=1.)
        late_mean = pm.Exponential('late_mean', lam=1.)
        # Allocate appropriate Poisson rates to years before and after current
        # switchpoint location
        idx = np.arange(years)
        rate = pm.switch(switchpoint >= idx, early_mean, late_mean)
        # Data likelihood
        pm.Poisson('disasters', rate, observed=disasters_data)
    return model


class TestDisasterModel(SeededTest):
    # Time series of recorded coal mining disasters in the UK from 1851 to 1962
    def test_disaster_model(self):
        model = build_disaster_model(masked=False)
        with model:
            # Initial values for stochastic nodes
            start = {'early_mean': 2., 'late_mean': 3.}
            # Use slice sampler for means (other varibles auto-selected)
            step = pm.Slice([model.early_mean_log_, model.late_mean_log_])
            tr = pm.sample(500, tune=50, start=start, step=step)
            pm.summary(tr)

    def test_disaster_model_missing(self):
        model = build_disaster_model(masked=True)
        with model:
            # Initial values for stochastic nodes
            start = {'early_mean': 2., 'late_mean': 3.}
            # Use slice sampler for means (other varibles auto-selected)
            step = pm.Slice([model.early_mean_log_, model.late_mean_log_])
            tr = pm.sample(500, tune=50, start=start, step=step)
            pm.summary(tr)


class TestATMIP_2gaussians(SeededTest):
    def setUp(self):
        self.trace_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.trace_dir)

    def build_model(self):
        dim = 4
        mu1 = 0.5 * np.ones(dim)
        mu2 = -mu1

        stdev = 0.1
        sigma = np.power(stdev, 2) * np.eye(dim)
        isigma = np.linalg.inv(sigma)
        dsigma = np.linalg.det(sigma)

        w1 = stdev
        w2 = (1 - stdev)

        def two_gaussians(x):
            log_like1 = - 0.5 * dim * tt.log(2 * np.pi) \
                        - 0.5 * tt.log(dsigma) \
                        - 0.5 * (x - mu1).T.dot(isigma).dot(x - mu1)
            log_like2 = - 0.5 * dim * tt.log(2 * np.pi) \
                        - 0.5 * tt.log(dsigma) \
                        - 0.5 * (x - mu2).T.dot(isigma).dot(x - mu2)
            return tt.log(w1 * tt.exp(log_like1) + w2 * tt.exp(log_like2))

        with pm.Model() as ATMIP_test:
            X = pm.Uniform('X',
                           shape=dim,
                           lower=-2. * np.ones_like(mu1),
                           upper=2. * np.ones_like(mu1),
                           testval=-1. * np.ones_like(mu1),
                           transform=None)
            like = pm.Deterministic('like', two_gaussians(X))
            pm.Potential('like', like)
        return ATMIP_test

    def still_broken(self):
        ATMIP_test = self.build_model()
        with ATMIP_test:
            step = pm.ATMCMC(n_chains=500, tune_interval=25,
                             likelihood_name=ATMIP_test.deterministics[0].name)

            trace = pm.ATMIP_sample(
                n_steps=50,
                step=step,
                njobs=1,
                progressbar=True,
                trace=self.trace_dir,
                )

        pm.summary(trace)


class TestGLMLinear(SeededTest):
    def build_model(self):
        size = 50
        true_intercept = 1
        true_slope = 2
        self.x = np.linspace(0, 1, size)
        self.y = true_intercept + self.x * true_slope + np.random.normal(scale=.5, size=size)
        data = dict(x=self.x, y=self.y)
        with pm.Model() as model:
            pm.glm.glm('y ~ x', data)
        return model

    def test_run(self):
        with self.build_model():
            start = pm.find_MAP(fmin=opt.fmin_powell)
            trace = pm.sample(50, pm.Slice(), start=start)

        pm.glm.plot_posterior_predictive(trace)


class TestHierarchical(SeededTest):
    @classmethod
    def setUpClass(cls):
        n_groups = 10
        no_pergroup = 30
        n_group_predictors = 1
        n_predictors = 3
        n_observed = no_pergroup * n_groups

        group = np.concatenate([[i] * no_pergroup for i in range(n_groups)])
        group_predictors = np.random.normal(size=(n_groups, n_group_predictors))
        predictors = np.random.normal(size=(n_observed, n_predictors))

        group_effects_a = np.random.normal(size=(n_group_predictors, n_predictors))
        effects_a = (np.random.normal(size=(n_groups, n_predictors)) +
                     np.dot(group_predictors, group_effects_a))

        y = np.sum(effects_a[group, :] * predictors, 1) + np.random.normal(size=(n_observed))
        with pm.Model() as cls.model:
            # m_g ~ N(0, .1)
            group_effects = pm.Normal("group_effects", 0, .1,
                                      shape=(1, n_group_predictors, n_predictors))
            # sg ~ Uniform(.05, 10)
            sg = pm.Uniform("sg", .05, 10, testval=2.)
            # m ~ N(mg * pg, sg)
            effects = pm.Normal("effects",
                                (group_predictors[:, :, np.newaxis] * group_effects).sum(),
                                sg ** -2,
                                shape=(n_groups, n_predictors))
            s = pm.Uniform("s", .01, 10, shape=n_groups)
            g = tt.constant(group)
            # y ~ Normal(m[g] * p, s)
            pm.Normal('y', (effects[g] * predictors).sum(), s[g] ** -2, observed=y)

    def test_normal(self):
        with self.model:
            start = pm.find_MAP()
            step = pm.NUTS(self.model.vars, scaling=start)
            pm.sample(50, step, start)

    def test_sqlite(self):
        with self.model:
            start = pm.find_MAP()
            step = pm.NUTS(self.model.vars, scaling=start)
            pm.sample(50, step, start, trace='sqlite')


class TestLatentOccupancy(SeededTest):
    """
    From the PyMC example list
    latent_occupancy.py

    Simple model demonstrating the estimation of occupancy, using latent variables. Suppose
    a population of n sites, with some proportion pi being occupied. Each site is surveyed,
    yielding an array of counts, y:

    y = [3, 0, 0, 2, 1, 0, 1, 0, ..., ]

    This is a classic zero-inflated count problem, where more zeros appear in the data than would
    be predicted by a simple Poisson model. We have, in fact, a mixture of models; one, conditional
    on occupancy, with a poisson mean of theta, and another, conditional on absence, with mean zero.
    One way to tackle the problem is to model the latent state of 'occupancy' as a Bernoulli
    variable at each site, with some unknown probability:

    z_i ~ Bern(pi)

    These latent variables can then be used to generate an array of Poisson parameters:

    t_i = theta (if z_i=1) or 0 (if z_i=0)

    Hence, the likelihood is just:

    y_i = Poisson(t_i)

    (Note in this elementary model, we are ignoring the issue of imperfect detection.)

    Created by Chris Fonnesbeck on 2008-07-28.
    Copyright (c) 2008 University of Otago. All rights reserved.
    """
    def setUp(self):
        # Sample size
        n = 100
        # True mean count, given occupancy
        theta = 2.1
        # True occupancy
        pi = 0.4
        # Simulate some data data
        self.y = (np.random.random(n) < pi) * np.random.poisson(lam=theta, size=n)

    def build_model(self):
        with pm.Model() as model:
            # Estimated occupancy
            psi = pm.Beta('psi', 1, 1)
            # Latent variable for occupancy
            pm.Bernoulli('z', psi, self.y.shape)
            # Estimated mean count
            theta = pm.Uniform('theta', 0, 100)
            # Poisson likelihood
            pm.ZeroInflatedPoisson('y', theta, psi, observed=self.y)
        return model

    def test_run(self):
        model = self.build_model()
        with model:
            start = {'psi': 0.5, 'z': (self.y > 0).astype(int), 'theta': 5}
            step_one = pm.Metropolis([model.theta_interval_, model.psi_logodds_])
            step_two = pm.BinaryMetropolis([model.z])
            pm.sample(50, [step_one, step_two], start)


class TestRSV(SeededTest):
    '''
    This model estimates the population prevalence of respiratory syncytial virus
    (RSV) among children in Amman, Jordan, based on 3 years of admissions diagnosed
    with RSV to Al Bashir hospital.

    To estimate this parameter from raw counts of diagnoses, we need to establish
    the population of  1-year-old children from which the diagnosed individuals
    were sampled. This involved correcting census data (national estimate of
    1-year-olds) for the proportion of the population in the city, as well as for
    the market share of the hospital. The latter is based on expert esimate, and
    hence encoded as a prior.
    '''
    def build_model(self):
        # 1-year-old children in Jordan
        kids = np.array([180489, 191817, 190830])
        # Proportion of population in Amman
        amman_prop = 0.35
        # infant RSV cases in Al Bashir hostpital
        rsv_cases = np.array([40, 59, 65])
        with pm.Model() as model:
            # Al Bashir hospital market share
            market_share = pm.Uniform('market_share', 0.5, 0.6)
            # Number of 1 y.o. in Amman
            n_amman = pm.Binomial('n_amman', kids, amman_prop, shape=3)
            # Prior probability
            prev_rsv = pm.Beta('prev_rsv', 1, 5, shape=3)
            # RSV in Amman
            y_amman = pm.Binomial('y_amman', n_amman, prev_rsv, shape=3, testval=100)
            # Likelihood for number with RSV in hospital (assumes Pr(hosp | RSV) = 1)
            pm.Binomial('y_hosp', y_amman, market_share, observed=rsv_cases)
        return model

    def test_run(self):
        with self.build_model():
            pm.sample(50, step=[pm.NUTS(), pm.Metropolis()])