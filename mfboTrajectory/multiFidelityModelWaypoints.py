#!/usr/bin/env python
# coding: utf-8

import numpy as np
import os, sys, time, copy
import yaml, h5py, shutil
from os import path
from pyDOE import lhs

from pyTrajectoryUtils.pyTrajectoryUtils.utils import *
from .trajSampler import TrajSampler

def meta_low_fidelity(poly, alpha_set, t_set_sta, points, debug=True, multicore=False, lb=0.6, ub=1.4):
    t_dim = t_set_sta.shape[0]
    lb_i = np.ones(t_dim)*lb
    ub_i = np.ones(t_dim)*ub
    label = np.zeros(alpha_set.shape[0])
    if multicore:
        data_list = []
        for it in range(alpha_set.shape[0]):
            alpha_tmp = lb_i + np.multiply(alpha_set[it,:],ub_i-lb_i)
            data_list.append((points, t_set_sta, alpha_tmp))
        results = parmap(poly.wrapper_sanity_check, data_list)
    else:
        results = []
        for it in range(alpha_set.shape[0]):
            alpha_tmp = lb_i + np.multiply(alpha_set[it,:],ub_i-lb_i)
            results.append(poly.wrapper_sanity_check((points, t_set_sta, alpha_tmp)))
        
    for it in range(alpha_set.shape[0]):
        if results[it]:
            label[it] = 1
            if debug:
                print("Succeeded")
        else:
            if debug:
                print("Failed")
    return label

def meta_high_fidelity(poly, alpha_set, t_set_sim, points, lb=0.6, ub=1.4, return_snap=False):
    t_dim = t_set_sim.shape[0]
    lb_i = np.ones(t_dim)*lb
    ub_i = np.ones(t_dim)*ub
    label = np.zeros(alpha_set.shape[0])
    if return_snap:
        snap_array = np.ones(alpha_set.shape[0])
    for it in range(alpha_set.shape[0]):
        alpha_tmp = lb_i + np.multiply(alpha_set[it,:],ub_i-lb_i)
        if return_snap:
            _, _, _, res_snap = poly.update_traj_(points, t_set_sim, alpha_tmp, flag_return_snap=True)
            snap_array[it] = res_snap
            continue
        
        t_set_tmp, d_ordered_tmp, d_ordered_yaw_tmp = poly.update_traj_(points, t_set_sim, alpha_tmp)
        if poly.run_sim_loop(t_set_tmp, d_ordered_tmp, d_ordered_yaw_tmp):
            label[it] = 1
    
    if return_snap:
        return snap_array
    else:
        return label

def check_dataset_init(name, t_dim, N_L=200, N_H=20, lb=0.6, ub=1.4, sampling_mode=1, dataset_dir="./mfbo_data", flag_robot=False):
    path_dataset_low = "{}/{}/low_fidelity_data_sta_{}_{}_smode{}.yaml" \
                    .format(dataset_dir,str(name),np.int(10*lb),np.int(10*ub),sampling_mode)
    
    if path.exists(path_dataset_low):
        X_L = []
        Y_L = []
        with open(path_dataset_low, "r") as input_stream:
            yaml_data_in = yaml.load(input_stream)
            alpha_sim = yaml_data_in["alpha_sim"]
            if flag_robot:
                alpha_robot = yaml_data_in["alpha_robot"]
            X_L_t = yaml_data_in["X_L"]
            Y_L_t = yaml_data_in["Y_L"]
            if len(Y_L_t) >= N_L:
                flag_generate_dataset = False
                X_L += X_L_t[:np.int(N_L/2)]
                Y_L += Y_L_t[:np.int(N_L/2)]
                X_L += X_L_t[np.int(len(Y_L_t)/2):np.int(len(Y_L_t)/2)+np.int(N_L/2)]
                Y_L += Y_L_t[np.int(len(Y_L_t)/2):np.int(len(Y_L_t)/2)+np.int(N_L/2)]

        X_L = np.array(X_L)
        Y_L = np.array(Y_L)

        X_H = []
        Y_H = []
        H_init_step = 1./N_H
        for i in range(np.int(N_H/2)):
            val = np.ones(t_dim)*(0.45-i*H_init_step)
            X_H.append(val)
            Y_H.append(0.)
        for i in range(np.int(N_H/2)):
            val = np.ones(t_dim)*(0.5+i*H_init_step)
            X_H.append(val)
            Y_H.append(1.)

        X_H = np.array(X_H)
        Y_H = np.array(Y_H)
        
        if flag_robot:
            return True, (alpha_sim, alpha_robot, X_L, Y_L, X_H, Y_H)
        else:
            return True, (alpha_sim, X_L, Y_L, X_H, Y_H)
    
    return False, None
        
def get_dataset_init(name, \
         alpha_sim, \
         low_fidelity, \
         high_fidelity, \
         t_dim, \
         N_L=200, N_H=20, \
         plot=False, \
         t_set_sim=None, \
         lb=0.6, ub=1.4, sampling_mode=1, \
         batch_size=100, dataset_dir="./mfbo_data", alpha_robot=None):

    path_dataset_low = "{}/{}/low_fidelity_data_sta_{}_{}_smode{}.yaml" \
                    .format(dataset_dir,str(name),np.int(10*lb),np.int(10*ub),sampling_mode)
    
    X_L_0 = np.empty((0,t_dim))
    X_L_1 = np.empty((0,t_dim))
    X_L = np.empty((0,t_dim))
    Y_L = np.empty(0)

    if sampling_mode == 0:
        sample_data = lambda N_sample: lhs(t_dim, N_sample)
    elif sampling_mode == 1:
        traj_sampler = TrajSampler(N=t_dim, sigma=0.2, flag_load=False, cov_mode=1, flag_pytorch=False)
        sample_data = lambda N_sample: traj_sampler.rsample(N_sample=N_sample)
    elif sampling_mode == 2:
        traj_sampler = TrajSampler(N=t_dim, sigma=0.2, flag_load=False, cov_mode=1, flag_pytorch=False)
        sample_data = lambda N_sample: np.concatenate((lhs(t_dim, N_sample),traj_sampler.rsample(N_sample=N_sample)),axis=0)
    elif sampling_mode == 3:
        traj_sampler = TrajSampler(N=t_dim, sigma=0.5, flag_load=False, cov_mode=1, flag_pytorch=False)
        sample_data = lambda N_sample: traj_sampler.rsample(N_sample=N_sample)
    elif sampling_mode == 4:
        traj_sampler = TrajSampler(N=t_dim, sigma=1.0, flag_load=False, cov_mode=1, flag_pytorch=False)
        sample_data = lambda N_sample: traj_sampler.rsample(N_sample=N_sample)
    elif sampling_mode == 5:
        traj_sampler = TrajSampler(N=t_dim, sigma=20.0, flag_load=False, cov_mode=1, flag_pytorch=False)
        sample_data = lambda N_sample: traj_sampler.rsample(N_sample=N_sample)
    elif sampling_mode == 6:
        traj_sampler = TrajSampler(N=t_dim, sigma=0.05, flag_load=False, cov_mode=1, flag_pytorch=False)
        sample_data = lambda N_sample: traj_sampler.rsample(N_sample=N_sample)
    else:
        raise("Not Implemented")

    while True:
        X_L_t = sample_data(batch_size)
        labels_low = low_fidelity(X_L_t, debug=False)
        Y_L_t = 1.0*labels_low
        if np.where(Y_L_t == 0)[0].shape[0] > 0:
            X_L_0 = np.concatenate((X_L_0, X_L_t[np.where(Y_L_t == 0)]))
        if np.where(Y_L_t > 0)[0].shape[0] > 0:
            X_L_1 = np.concatenate((X_L_1, X_L_t[np.where(Y_L_t > 0)]))
        print("N_L_0: {}, N_L_1: {}".format(X_L_0.shape[0],X_L_1.shape[0]))
        if X_L_0.shape[0] >= N_L/2 and X_L_1.shape[0] >= N_L/2:
            X_L = np.concatenate((X_L_0[:np.int(N_L/2),:],X_L_1[:np.int(N_L/2),:]))
            Y_L = np.zeros(N_L)
            Y_L[np.int(N_L/2):] = 1
            break

    directory = os.path.dirname(path_dataset_low)
    if not os.path.exists(directory):
        os.makedirs(directory)
    yaml_data = {"X_L":X_L, "Y_L":Y_L}
    yamlFile = path_dataset_low
    yaml_out = open(yamlFile,"w")
    yaml_out.write("alpha_sim: {}\n\n".format(alpha_sim))
    if np.all(alpha_robot != None):
        yaml_out.write("alpha_robot: {}\n\n".format(alpha_robot))
    yaml_out.write("X_L:\n")
    for it in range(X_L.shape[0]):
        yaml_out.write("  - [{}]\n".format(', '.join([str(x) for x in X_L[it,:]])))
    yaml_out.write("\n")
    yaml_out.write("Y_L: [{}]\n\n".format(', '.join([str(x) for x in Y_L])))
    yaml_out.close()

    X_L = np.array(X_L)
    Y_L = np.array(Y_L)

    X_H = []
    Y_H = []
    H_init_step = 1./N_H
    for i in range(np.int(N_H/2)):
        val = np.ones(t_dim)*(0.45-i*H_init_step)
        X_H.append(val)
        Y_H.append(0.)
    for i in range(np.int(N_H/2)):
        val = np.ones(t_dim)*(0.5+i*H_init_step)
        X_H.append(val)
        Y_H.append(1.)

    X_H = np.array(X_H)
    Y_H = np.array(Y_H)
    
    if plot and t_set_sim is not None:
        fig = plt.figure(1,figsize=(5,5))
        plt.clf()
        fig.set_size_inches((5,5))
        ax = plt.subplot(111)
        
        X_L_denorm_ = lb + X_L*(ub-lb)
        X_L_time_ = np.multiply(X_L_denorm_, np.repeat(np.expand_dims(t_set_sim,0),X_L_denorm_.shape[0],axis=0))
        X_H_denorm_ = lb + X_H*(ub-lb)
        X_H_time_ = np.multiply(X_H_denorm_, np.repeat(np.expand_dims(t_set_sim,0),X_H_denorm_.shape[0],axis=0))
        
        plt.scatter(X_L_time_[:,0], X_L_time_[:,1], c = Y_L, cmap='coolwarm_r', marker = 'x', label = 'low fidelity sample')
        plt.scatter(X_H_time_[:,0], X_H_time_[:,1], c = Y_H, cmap='coolwarm_r', label = 'high fidelity sample')
        plt.legend(frameon = False)
        
        plt.xlim([lb*t_set_sim[0],ub*t_set_sim[0]])
        plt.ylim([lb*t_set_sim[1],ub*t_set_sim[1]])
        ax.xaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))

        prettyplot("$\mathregular{x_1}$ [s]", "$\mathregular{x_2}$ [s]", ylabelpad=-15)
        plt.tight_layout()
        plt.savefig("./mfbo_data/rand_mini1_yaw/init_data.png")
        plt.close()
#         plt.show()
        
    return X_L, Y_L, X_H, Y_H