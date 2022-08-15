"""Matthew Syzdagis' mock-up of the XENONnT detector implementation

"""
import numpy as np
import tensorflow as tf

import configparser
import os

import flamedisx as fd
from .. import nest as fd_nest

import math as m
pi = tf.constant(m.pi)

export, __all__ = fd.exporter()
o = tf.newaxis


##
# Flamedisx sources
##


class XENONnTSource:
    path_s1_rly = 'nt_maps/XnT_S1_xyz_MLP_v0.3_B2d75n_C2d75n_G0d3p_A4d9p_T0d9n_PMTs1d3n_FSR0d65p_v0d677.json'
    path_s2_rly = 'nt_maps/XENONnT_s2_xy_map_v4_210503_mlp_3_in_1_iterated.json'

    def __init__(self, *args, **kwargs):
        assert kwargs['detector'] in ('XENONnT',)

        assert os.path.exists(os.path.join(
            os.path.dirname(__file__), '../nest/config/', kwargs['detector'] + '.ini'))

        config = configparser.ConfigParser(inline_comment_prefixes=';')
        config.read(os.path.join(os.path.dirname(__file__), '../nest/config/',
                                 kwargs['detector'] + '.ini'))

        self.density = fd_nest.calculate_density(
            config.getfloat('NEST', 'temperature_config'),
            config.getfloat('NEST', 'pressure_config')).item()
        self.drift_velocity = fd_nest.calculate_drift_velocity(
         config.getfloat('NEST', 'drift_field_config'),
         self.density,
         config.getfloat('NEST', 'temperature_config')).item()

        self.cS1_min = config.getfloat('NEST', 'cS1_min_config')
        self.cS1_max = config.getfloat('NEST', 'cS1_max_config')
        self.cS2_min = config.getfloat('NEST', 'cS2_min_config')
        self.cS2_max = config.getfloat('NEST', 'cS2_max_config')

        try:
            self.s1_map = fd.InterpolatingMap(fd.get_nt_file(self.path_s1_rly))
            self.s2_map = fd.InterpolatingMap(fd.get_nt_file(self.path_s2_rly))
        except Exception:
            print("Could not load maps; setting position corrections to 1")
            self.s1_map = None
            self.s2_map = None

        super().__init__(*args, **kwargs)

        self.extraction_eff = 0.52
        self.z_top = config.getfloat('NEST', 'z_top_config')
        self.z_bottom = config.getfloat('NEST', 'z_bottom_config')

    def draw_positions(self, n_events, **params):
        """Return dictionary with x, y, z, r, theta, drift_time
        randomly drawn.
        """
        data = dict()
        data['r'] = (np.random.rand(n_events) * self.radius**2)**0.5
        data['theta'] = np.random.uniform(0, 2*np.pi, size=n_events)
        data['z'] = np.random.uniform(self.z_bottom, self.z_top,
                                      size=n_events)
        data['x'], data['y'] = fd.pol_to_cart(data['r'], data['theta'])

        data['drift_time'] = -data['z'] / self.drift_velocity
        return data

    def validate_fix_truth(self, d):
        """Clean fix_truth, ensure all needed variables are present
           Compute derived variables.
        """
        # When passing in an event as DataFrame we select and set
        # only these columns:
        cols = ['x', 'y', 'z', 'r', 'theta', 'event_time', 'drift_time']
        if d is None:
            return dict()
        elif isinstance(d, pd.DataFrame):
            # This is useful, since it allows you to fix_truth with an
            # observed event.
            # Assume fix_truth is a one-line dataframe with at least
            # cols columns
            return d[cols].iloc[0].to_dict()
        elif isinstance(d, pd.Series):
            # This is useful, since it allows you to fix_truth with an
            # observed event.
            # Assume fix_truth is a one-line series with at least
            # cols columns
            return d[cols].to_dict()
        else:
            assert isinstance(d, dict), \
                "fix_truth needs to be a DataFrame, dict, or None"

        if 'z' in d:
            # Position is fixed. Ensure both Cartesian and polar coordinates
            # are available, and compute drift_time from z.
            if 'x' in d and 'y' in d:
                d['r'], d['theta'] = fd.cart_to_pol(d['x'], d['y'])
            elif 'r' in d and 'theta' in d:
                d['x'], d['y'] = fd.pol_to_cart(d['r'], d['theta'])
            else:
                raise ValueError("When fixing position, give (x, y, z), "
                                 "or (r, theta, z).")
            d['drift_time'] = -d['z'] / self.drift_velocity
        elif 'event_time' not in d and 'energy' not in d:
            # Neither position, time, nor energy given
            raise ValueError(f"Dict should contain at least ['x', 'y', 'z'] "
                             "and/or ['r', 'theta', 'z'] and/or 'event_time' "
                             f"and/or 'energy', but it contains: {d.keys()}")
        return d

    @staticmethod
    def electron_gain_mean(s2_relative_ly):
        elYield = 31.2
        return tf.cast(elYield, fd.float_type()) * s2_relative_ly

    def electron_gain_std(self):
        elYield = 31.2
        return tf.sqrt(self.s2Fano * elYield)[o]

    @staticmethod
    def photon_detection_eff(z, *, g1=0.126):
        return g1 * tf.ones_like(z)

    @staticmethod
    def s2_photon_detection_eff(z, *, g1_gas=0.851):
        return g1_gas * tf.ones_like(z)

    def s1_posDependence(self, s1_relative_ly):
        return s1_relative_ly

    def s2_posDependence(self, r):
        return tf.ones_like(r)

    def s1_acceptance(self, s1, cs1):
        return tf.where((s1 < self.S1_min) | (s1 < self.spe_thr) | (s1 > self.S1_max) | \
                        (cs1 < self.cS1_min) | (cs1 > self.cS1_max),
                        tf.zeros_like(s1, dtype=fd.float_type()),
                        tf.ones_like(s1, dtype=fd.float_type()))

    def s2_acceptance(self, s2, cs2):
        return tf.where((s2 < self.S2_min) | (s2 > self.S2_max) | \
                        (cs2 < self.cS2_min) | (cs2 > self.cS2_max),
                        tf.zeros_like(s2, dtype=fd.float_type()),
                        tf.ones_like(s2, dtype=fd.float_type()))

    def add_extra_columns(self, d):
        super().add_extra_columns(d)

        if (self.s1_map is not None) and (self.s2_map is not None):
            d['s1_relative_ly'] = self.s1_map(
                np.transpose([d['x'].values,
                              d['y'].values,
                              d['z'].values]))
            d['s2_relative_ly'] = self.s2_map(
                np.transpose([d['x'].values,
                              d['y'].values]))
        else:
            d['s1_relative_ly'] = np.ones_like(d['x'].values)
            d['s2_relative_ly'] = np.ones_like(d['x'].values)

        if 's1' in d.columns:
            d['cs1'] = d['s1'] / d['s1_relative_ly']
        if 's2' in d.columns:
            d['cs2'] = (
                d['s2']
                / d['s2_relative_ly']
                * np.exp(d['drift_time'] / self.elife))


@export
class XENONnTERSource(XENONnTSource, fd.nest.nestERSource):
    def __init__(self, *args, **kwargs):
        if ('detector' not in kwargs):
            kwargs['detector'] = 'XENONnT'
        super().__init__(*args, **kwargs)

    def variance(self, *args):
        nel_mean = args[0]
        nq_mean = args[1]
        recomb_p = args[2]
        ni = args[3]

        er_free_b = 0.05
        er_free_c = 0.205
        er_free_d = 0.45
        er_free_e = -0.2

        elec_frac = nel_mean / nq_mean
        ampl = tf.cast(0.086036 + (er_free_b - 0.086036) /
                       pow((1. + pow(self.drift_field / 295.2, 251.6)), 0.0069114),
                       fd.float_type())
        wide = er_free_c
        cntr = er_free_d
        skew = er_free_e

        mode = cntr + 2. / (tf.sqrt(2. * pi)) * skew * wide / tf.sqrt(1. + skew * skew)
        norm = 1. / (tf.exp(-0.5 * pow(mode - cntr, 2.) / (wide * wide)) *
                     (1. + tf.math.erf(skew * (mode - cntr) / (wide * tf.sqrt(2.)))))

        omega = norm * ampl * tf.exp(-0.5 * pow(elec_frac - cntr, 2.) / (wide * wide)) * \
            (1. + tf.math.erf(skew * (elec_frac - cntr) / (wide * tf.sqrt(2.))))
        omega = tf.where(nq_mean == 0,
                         tf.zeros_like(omega, dtype=fd.float_type()),
                         omega)

        return recomb_p * (1. - recomb_p) * ni + omega * omega * ni * ni


@export
class XENONnTNRSource(XENONnTSource, fd.nest.nestNRSource):
    def __init__(self, *args, **kwargs):
        if ('detector' not in kwargs):
            kwargs['detector'] = 'XENONnT'
        super().__init__(*args, **kwargs)

    @staticmethod
    def yield_fano(nq_mean):
        nr_free_a = 0.4
        nr_free_b = 0.4

        ni_fano = tf.ones_like(nq_mean, dtype=fd.float_type()) * nr_free_a
        nex_fano = tf.ones_like(nq_mean, dtype=fd.float_type()) * nr_free_b

        return ni_fano, nex_fano

    @staticmethod
    def variance(*args):
        nel_mean = args[0]
        nq_mean = args[1]
        recomb_p = args[2]
        ni = args[3]

        nr_free_c = 0.04
        nr_free_d = 0.5
        nr_free_e = 0.19

        elec_frac = nel_mean / nq_mean

        omega = nr_free_c * tf.exp(-0.5 * pow(elec_frac - nr_free_d, 2.) / (nr_free_e * nr_free_e))
        omega = tf.where(nq_mean == 0,
                         tf.zeros_like(omega, dtype=fd.float_type()),
                         omega)

        return recomb_p * (1. - recomb_p) * ni + omega * omega * ni * ni