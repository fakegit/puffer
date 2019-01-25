#!/usr/bin/env python3

import sys
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from collect_data import collect_video_data, VIDEO_DURATION
import ttp

BIN_SIZE = 0.5
BIN_MAX = 20
TCP_INFO = ['delivery_rate', 'cwnd', 'in_flight', 'min_rtt', 'rtt']
TCP_SETTINGS = {'no cwnd in_flight': [8, ['delivery_rate', 'min_rtt', 'rtt']],
                'no deliver_rate': [8, ['cwnd', 'in_flight', 'min_rtt', 'rtt']],
                'no rtt': [8, ['delivery_rate', 'cwnd', 'in_flight']],
                'no tcp_info': [8, []],
                'origin': [8, TCP_INFO],
               }

MODEL_PATH = '/home/ubuntu/models'
TCP_PATHS = {'no cwnd in_flight': \
                '/ablation/0101-0115-no-cwndinflight-bbr',
            'no deliver_rate': \
                '/ablation/0101-0115-no-delirate-bbr',
            'no rtt': \
                '/ablation/0101-0115-no-rtt-bbr',
            'no tcp_info': \
                '/ablation/0101-0115-no-tcp-info-bbr',
            'origin': \
                '/ablation/0101-0115-past8-bbr',
           }

PC_SETTINGS = {'past 1 chunk': [1, TCP_INFO],
               'past 2 chunks': [2, TCP_INFO],
               'past 4 chunks': [4, TCP_INFO],
               'past 8 chunks': [8, TCP_INFO],
               'past 16 chunks': [16, TCP_INFO],
               'continue': [8, TCP_INFO],
              }
PC_PATHS = {'past 1 chunk': \
                '/ablation/0101-0115-past1-bbr',
            'past 2 chunks': \
                '/ablation/0101-0115-past2-bbr',
            'past 4 chunks': \
                '/ablation/0101-0115-past4-bbr',
            'past 8 chunks': \
                '/ablation/0101-0115-past8-bbr',
            'past 16 chunks': \
                '/ablation/0101-0115-past16-bbr',
            'continue': \
                '/puffer-models-0122/bbr-20190122-1',
           }


def error(estimate, real):
    return estimate - real


def abs_error(estimate, real):
    dis_est = int((estimate + 0.5 * BIN_SIZE) / BIN_SIZE)
    dis_real = int((real + 0.5 * BIN_SIZE) / BIN_SIZE)
    return int(dis_est != dis_real)


def ttp_discretized(trans_time):
    return int((trans_time + 0.5 * BIN_SIZE) / BIN_SIZE)

def ttp_map_dis_to_real(dis_time):
    if dis_time == 0:
        return BIN_SIZE * 0.25
    else:
        return dis_time * BIN_SIZE


def pred_error(dst, est_tput, verbose=False):
    assert(est_tput is not None)

    est_trans_time = dst['size'] / est_tput
    real_trans_time = dst['trans_time']

    dis_est = ttp_map_dis_to_real(ttp_discretized(est_trans_time))
    dis_real = ttp_map_dis_to_real(ttp_discretized(real_trans_time))

    if verbose:
        print(est_trans_time, ' ', real_trans_time)

    return abs(error(dis_est, dis_real))


def prepare_ttp_input(sess, ts, model):
    in_raw = ttp.prepare_input(sess, ts,
                               ttp.prepare_input_pre(sess, ts, model))

    assert(len(in_raw) == model.dim_in)
    input_data = model.normalize_input([in_raw], update_obs=False)

    return input_data


# the error using maximum likelihood estimation
def MLE_error(sess, ts, model):
    input_data = prepare_ttp_input(sess, ts, model)
    model.set_model_eval()
    pred = model.predict(input_data)
    return pred_error(sess[ts], sess[ts]['size'] / pred[0])

# the error using cross entropy loss
def CE_error(sess, ts, model):
    input_data = prepare_ttp_input(sess, ts, model)
    model.set_model_eval()
    scores = model.predict_distr(input_data).reshape(-1)
    dis_real = min(BIN_MAX, ttp_discretized(sess[ts]['trans_time']))
    return - np.log(scores[dis_real])


# HM prediction
def harmonic_mean(sess, ts):
    past_tputs = []

    for i in range(1, 6):
        prev_ts = ts - i * VIDEO_DURATION
        if prev_ts not in sess:
            return None

        prev_tput = sess[prev_ts]['size'] / sess[prev_ts]['trans_time']
        past_tputs.append(prev_tput)  # byte/second

        hm_tput = len(past_tputs) / np.sum(1 / np.array(past_tputs))
    return hm_tput



def calc_pred_error(d, models, error_func):
    midstream_err = {setting: [] for setting in models}
    midstream_err['HM'] = []

    for session in d:
        for ts in d[session]:
            dst = d[session][ts]
            for setting in models:
                if error_func == 'Absolute':
                    midstream_err[setting].append( \
                        MLE_error(d[session], ts, models[setting]))

            est = harmonic_mean(d[session], ts)
            if est == None:
                continue
            if error_func == 'Absolute':
                midstream_err['HM'].append(pred_error(dst, est))

    return midstream_err



def plot_error_cdf(error_dict, time_start, time_end, xlabel):
    fig, ax = plt.subplots()

    x_min = 0
    x_max = 1
    num_bins = 100
    for estimator, error_list in error_dict.items():
        if not error_list:
            continue

        counts, bin_edges = np.histogram(error_list, bins=num_bins,
                                         range=(x_min, x_max))
        x = bin_edges
        y = np.cumsum(counts) / len(error_list)
        y = np.insert(y, 0, 0)  # prepend 0

        ax.plot(x, y, label=estimator)

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0, 1)
    ax.legend()
    ax.grid()

    title = ('Transmission Time Prediction Accuracy\n[{}, {}] (UTC)'
             .format(time_start, time_end))
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel('CDF')

    figname = 'ablation.png'
    fig.savefig(figname, dpi=150, bbox_inches='tight')
    sys.stderr.write('Saved plot to {}\n'.format(figname))


def print_statistic(errors):
    for term in errors:
        error = numpy.array(errors[term])
        print(tmp, ':', error.mean(), ',', error.std())


def load_models(i, settings, paths):
    models = {}

    for setting in settings:
        model_path = MODEL_PATH + paths[setting] + '/py-{}.pt'.format(i)

        model = ttp.Model(past_chunks=settings[setting][0],
                          tcp_info=settings[setting][1])
        model.load(model_path)
        sys.stderr.write('Loaded ttp model {} from {}\n' \
                         .format(setting, model_path))

        models[setting] = model

    return models


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('yaml_settings')
    parser.add_argument('--from', dest='time_start',
                        help='datetime in UTC conforming to RFC3339')
    parser.add_argument('--to', dest='time_end',
                        help='datetime in UTC conforming to RFC3339')
    parser.add_argument('--cc', help='filter input data by congestion control')
    parser.add_argument('--error-func', help='set different error function')
    parser.add_argument('--ablation', help='set ablation term')
    args = parser.parse_args()

    video_data = collect_video_data(args.yaml_settings,
                                    args.time_start, args.time_end, args.cc)

    # for ablation study of tcp_info
    models = None
    if args.ablation == 'tcp_info':
        models = load_models(0, TCP_SETTINGS, TCP_PATHS)
    if args.ablation == 'past_chunks':
        models = load_models(0, PC_SETTINGS, PC_PATHS)

    # choose error func
    xlabel = None
    if args.error_func == 'Absolute':
        xlabel = 'Absolute Predict Error'
    if args.error_func == 'CE':
        xlabel = 'CE predict error'

    midstream_err = calc_pred_error(video_data, models, args.error_func)

    # print the statistic
    print_statistic(midstream_err)

    # plot CDF graph of mistream prediction errors
    plot_error_cdf(midstream_err, args.time_start, args.time_end, xlabel)

if __name__ == '__main__':
    main()
