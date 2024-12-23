#!/usr/bin/env python
# coding: utf-8

import numpy as np
import sys, os, copy, time
import yaml
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter
from tensorboardX import SummaryWriter
from pyDOE import lhs

import torch
from torch.utils.data import TensorDataset, DataLoader
# from torch.nn import Linear
# import torch.nn as nn
# import torch.nn.functional as F

import gpytorch
# from gpytorch.means import ConstantMean
# from gpytorch.kernels import RBFKernel, ScaleKernel, LinearKernel
# from gpytorch.variational import VariationalStrategy, CholeskyVariationalDistribution
# from gpytorch.distributions import MultivariateNormal
# from gpytorch.models import AbstractVariationalGP, GP
from gpytorch.mlls import VariationalELBO#, AddedLossTerm
# from gpytorch.likelihoods import GaussianLikelihood, BernoulliLikelihood
# from gpytorch.models.deep_gps import AbstractDeepGPLayer, AbstractDeepGP, DeepLikelihood

from pyTrajectoryUtils.pyTrajectoryUtils.utils import *
from .models import *
from .trajSampler import TrajSampler, gaussian_sampler
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
class MFBOAgentBase():
    def __init__(self, *args, **kwargs):
        """
        Initialize the agent with given parameters.
        Parameters:
        - X_L (np.ndarray): Low-fidelity input data.
        - Y_L (np.ndarray): Low-fidelity output data.
        - lb_i (np.ndarray): Lower bounds for input space.
        - ub_i (np.ndarray): Upper bounds for input space.
        - rand_seed (int): Random seed for reproducibility.
        - C_L (np.ndarray): Low-fidelity cost data.
        - sampling_func_L (callable): Sampling function for low-fidelity data.
        - t_set_sim (np.ndarray): Time set for simulation.
        - traj_wp_sampler_mean (float): Mean for trajectory waypoint sampler.
        - traj_wp_sampler_var (float): Variance for trajectory waypoint sampler.
        - delta_L (float): Delta parameter for low-fidelity data.
        - beta (float): Beta parameter.
        - iter_create_model (int): Number of iterations to create model.
        - N_cand (int): Number of candidate samples.
        - utility_mode (int): Mode for utility function.
        - sampling_mode (int): Mode for sampling function.
        - model_prefix (str): Prefix for model name.
        - writer (SummaryWriter): TensorBoard summary writer.

        Raises:
        - Exception: If an unsupported sampling mode is provided.
        """
        # 
        self.X_L = kwargs.get('X_L', None)
        self.Y_L = kwargs.get('Y_L', None)
        self.N_L = self.X_L.shape[0]
        self.lb_i = kwargs.get('lb_i', None)
        self.ub_i = kwargs.get('ub_i', None)
        self.rand_seed = kwargs.get('rand_seed', None)
        self.C_L = kwargs.get('C_L', None)
        self.C_H = kwargs.get('C_H', None)
        self.sampling_func_L = kwargs.get('sampling_func_L', None)
        self.t_set_sim = kwargs.get('t_set_sim', None)
        self.traj_wp_sampler_mean = kwargs.get('traj_wp_sampler_mean', 0.5)
        self.traj_wp_sampler_var = kwargs.get('traj_wp_sampler_var', 0.2)
        
        self.delta_L = kwargs.get('delta_L', 0.8)
        # self.delta_H = kwargs.get('delta_H', 0.4)
        self.beta = kwargs.get('beta', 0.05)
        self.dim = self.X_L.shape[1]
        self.iter_create_model = kwargs.get('iter_create_model', 200)
        self.N_cand = kwargs.get('N_cand', 1000)

        self.utility_mode = kwargs.get('utility_mode', 0)
        self.sampling_mode = kwargs.get('sampling_mode', 0)
        self.model_prefix = kwargs.get('model_prefix', 'mfbo_test')
        self.writer = SummaryWriter('runs/mfbo/'+self.model_prefix)

        self.rel_snap_array = [1]
        self.exp_result_array = [1]

        
        np.random.seed(self.rand_seed)
        torch.manual_seed(self.rand_seed)
        
        self.t_dim = self.t_set_sim.shape[0]
        self.p_dim = 0
        
        if self.sampling_mode == 0:
            self.sample_data = lambda N_sample: lhs(self.t_dim, N_sample) # https://pythonhosted.org/pyDOE/randomized.html   
        elif self.sampling_mode == 1:
            self.traj_sampler = TrajSampler(N=self.t_dim, sigma=50.0)
            self.sample_data = lambda N_sample: self.traj_sampler.rsample(N_sample=N_sample)
        elif self.sampling_mode == 2:
            self.traj_sampler = TrajSampler(N_sample=N_cand, N=self.t_dim, sigma=50.0, flag_load=True)
            self.sample_data = lambda N_sample: self.traj_sampler.rsample(N_sample=N_sample)
        elif self.sampling_mode == 3:
            self.traj_sampler = TrajSampler(N_sample=N_cand, N=self.t_dim, sigma=1.0, flag_load=True, cov_mode=1, flag_pytorch=False)
            self.sample_data = lambda N_sample: self.traj_sampler.rsample(N_sample=N_sample)
        elif self.sampling_mode == 4:
            self.traj_sampler = TrajSampler(N_sample=N_cand, N=self.t_dim, sigma=0.5, flag_load=True, cov_mode=1, flag_pytorch=False)
            self.sample_data = lambda N_sample: self.traj_sampler.rsample(N_sample=N_sample)
        elif self.sampling_mode == 5:
            self.traj_sampler = TrajSampler(N_sample=4096, N=self.t_dim, sigma=0.2, flag_load=True, cov_mode=1, flag_pytorch=False)
            self.sample_data = lambda N_sample: self.traj_sampler.rsample(N_sample=N_sample)
        elif self.sampling_mode == 6:
            self.traj_sampler = TrajSampler(N_sample=N_cand, N=self.t_dim, sigma=20.0, flag_load=True, cov_mode=1, flag_pytorch=False)
            self.sample_data = lambda N_sample: self.traj_sampler.rsample(N_sample=N_sample)
        elif self.sampling_mode == 7:
            self.sample_data = lambda N_sample: lhs(self.t_dim, N_sample)
        else:
            raise "Not implemented"
        self.X_cand = self.sample_data(self.N_cand) # TODO FIGURE OUT THE EXACT CONTENTS OF X_CAND BECASUE ITS INDEXED LATER WITH HIGH AND LOW FIDELTIY INDICES
        
        self.min_time = 1.0
        self.min_time_cand = 1.0
        self.alpha_min = np.ones(self.X_cand.shape[1])
        self.alpha_min_cand = np.ones(self.X_cand.shape[1])
        self.flag_found_ei = False
        
        self.X_test = np.zeros((2500,2))
        xx, yy = np.meshgrid(np.linspace(0,1,50,endpoint=True),np.linspace(0,1,50,endpoint=True))
        self.X_test[:,0] = xx.reshape(-1)
        self.X_test[:,1] = yy.reshape(-1)
        
    def load_exp_data(self, \
          filedir='./mfbo_data/', \
          filename='exp_data.yaml'):
        
        yamlFile = os.path.join(filedir, filename)
        with open(yamlFile, "r") as input_stream:
            yaml_in = yaml.load(input_stream)
            self.start_iter = np.int(yaml_in["start_iter"])
            self.X_L = np.array(yaml_in["X_L"])
            self.Y_L = np.array(yaml_in["Y_L"])
            self.N_L = self.X_L.shape[0]
            # self.X_H = np.array(yaml_in["X_H"])
            # self.Y_H = np.array(yaml_in["Y_H"])
            # self.N_H = self.X_H.shape[0]
            self.X_cand = np.array(yaml_in["X_cand"])
#             self.X_cand_H = np.array(yaml_in["X_cand_H"])
            self.min_time_array = yaml_in["min_time_array"]
            self.alpha_cand_array = yaml_in["alpha_cand_array"]
            self.fidelity_array = yaml_in["fidelity_array"]
            self.found_ei_array = yaml_in["found_ei_array"]
            self.exp_result_array = yaml_in["exp_result_array"]
            self.rel_snap_array = yaml_in["rel_snap_array"]
            self.alpha_min = np.array(yaml_in["alpha_min"])
            self.min_time = np.float(self.min_time_array[-1])
            self.N_low_fidelity = np.int(yaml_in["N_low_fidelity"])
            
            prGreen("#################################################")
            prGreen("Exp data loaded. start_iter: {}, N_L: {}"\
                    .format(self.start_iter, self.Y_L.shape[0]))
            # prGreen("Exp data loaded. start_iter: {}, N_L: {}, N_H: {}"\
            #         .format(self.start_iter, self.Y_L.shape[0], self.Y_H.shape[0]))
            prGreen("#################################################")
    
    def save_exp_data(self, \
                  filedir='./mfbo_data/', \
                  filename='exp_data.yaml'):
                
        yamlFile = os.path.join(filedir, filename)
        yaml_out = open(yamlFile,"w")
        yaml_out.write("start_iter: {}\n\n".format(self.start_iter))
        
        yaml_out.write("X_L:\n")
        for it in range(self.X_L.shape[0]):
            yaml_out.write("  - [{}]\n".format(', '.join([str(x) for x in self.X_L[it,:]])))
        yaml_out.write("\n")
        yaml_out.write("Y_L: [{}]\n".format(', '.join([str(x) for x in self.Y_L])))
        yaml_out.write("\n")
        
        # yaml_out.write("X_H:\n")
        # for it in range(self.X_H.shape[0]):
        #     yaml_out.write("  - [{}]\n".format(', '.join([str(x) for x in self.X_H[it,:]])))
        # yaml_out.write("\n")
        # yaml_out.write("Y_H: [{}]\n".format(', '.join([str(x) for x in self.Y_H])))
        # yaml_out.write("\n")
        
        yaml_out.write("X_cand:\n")
        for it in range(self.X_cand.shape[0]):
            yaml_out.write("  - [{}]\n".format(', '.join([str(x) for x in self.X_cand[it,:]])))
        yaml_out.write("\n")
#         yaml_out.write("X_cand_H:\n")
#         for it in range(self.X_cand_H.shape[0]):
#             yaml_out.write("  - [{}]\n".format(', '.join([str(x) for x in self.X_cand_H[it,:]])))
#         yaml_out.write("\n")
        
        yaml_out.write("min_time_array: [{}]\n".format(', '.join([str(x) for x in self.min_time_array])))
        yaml_out.write("\n")
        yaml_out.write("alpha_cand_array:\n")
        for it in range(len(self.alpha_cand_array)):
            yaml_out.write("  - [{}]\n".format(', '.join([str(x) for x in self.alpha_cand_array[it]])))
        yaml_out.write("\n")
        yaml_out.write("fidelity_array: [{}]\n".format(', '.join([str(x) for x in self.fidelity_array])))
        yaml_out.write("\n")
        yaml_out.write("found_ei_array: [{}]\n".format(', '.join([str(x) for x in self.found_ei_array])))
        yaml_out.write("\n")
        yaml_out.write("exp_result_array: [{}]\n".format(', '.join([str(x) for x in self.exp_result_array])))
        yaml_out.write("\n")
        yaml_out.write("rel_snap_array: [{}]\n".format(', '.join([str(x) for x in self.rel_snap_array])))
        yaml_out.write("\n")
        yaml_out.write("alpha_min: [{}]\n".format(', '.join([str(x) for x in self.alpha_min])))
        yaml_out.write("\n")
        yaml_out.write("N_low_fidelity: {}\n".format(self.N_low_fidelity))
        yaml_out.write("\n")
        yaml_out.close()
        
    def create_model(self):
        raise "Not Implemented"
    
    def forward_cand(self):
        raise "Not Implemented"
    
    # # gets x from X using acquisition function (line 20)
    # # self.X_next = x_i 
    # # self.X_cand = X
    # # TO DO update acquisition functions for multi agent
    # def compute_next_point_cand(self):
    #     mean, var, prob_cand, prob_cand_mean = self.forward_cand()
        
    #     # TO DO is eq 17 from mfbo, need to modify for eq 21
    #     alpha_explore = -np.abs(mean)/(var + 1e-9)*self.C_L
        
    #     # find alpha_exploit following mfbo eq. 19
    #     self.flag_found_ei = False
    #     max_ei_idx_L = -1  # -1 if alpha_exploit not updated
    #     max_ei_L = 0  # alpha_exploit initially set to 0
    #     min_time_tmp = self.min_time  # xbar
    #     # check if there exists x in X where alpha_exploit(x) > 0
    #     for it in range(self.X_cand.shape[0]):
    #         x_cand_denorm = self.lb_i + np.multiply(self.X_cand[it,:self.t_dim],self.ub_i-self.lb_i)
    #         min_time_tmp2 = x_cand_denorm.dot(self.t_set_sim)/np.sum(self.t_set_sim)
    #         max_ei_tmp_L = (self.min_time-min_time_tmp2)*prob_cand[it]  # alpha_ei * p(y=1|x)
    #         if max_ei_tmp_L > max_ei_L and prob_cand[it] > 1-self.delta_L: # if new alpha_exploit is larger than old one & prob >= h
    #             max_ei_L = max_ei_tmp_L  # update alpha_exploit
    #             max_ei_idx_L = it

    #     self.X_next_fidelity = 0  # TO DO remove all instances
    #     # alpha_exploit
    #     if max_ei_idx_L != -1:
    #         self.flag_found_ei = True
    #         self.X_next = self.X_cand[max_ei_idx_L,:]
    #         self.min_time_cand = min_time_tmp
    #         print(f"alpha_exploit: {max_ei_L}")
    #     # alpha_explore
    #     else:
    #         self.X_next = self.X_cand[alpha_explore.argmax()] ### LINE 6 IN ALGORITHM 1 ###
    #         x_cand_denorm = self.lb_i + np.multiply(self.X_cand[ent_H.argmax(),:self.t_dim],self.ub_i-self.lb_i) # TODO figure out what to change here for candidate stuff
    #         self.min_time_cand = x_cand_denorm.dot(self.t_set_sim)/np.sum(self.t_set_sim)
    #         print(f"alpha_explore: {alpha_explore}")
    #     self.alpha_min_cand = self.lb_i + np.multiply(self.X_next[:,:self.t_dim],self.ub_i-self.lb_i)

    # add points to dataset
    # sample more points for X_cand
    # TO DO need to split points into correct datasets ?
    # TO DO change how we generate X_cand
    def append_next_point(self, X_next, Y_next):
        # X_next_denorm = self.lb_i + np.multiply(self.X_next[:self.t_dim],self.ub_i-self.lb_i)
        # X_next_time = X_next_denorm.dot(self.t_set_sim)/np.sum(self.t_set_sim)
        # print("X_next: {}".format(X_next_denorm))
        # print("X_next time: {}".format(X_next_time))
        # self.N_low_fidelity += 1
        # print("low fidelity: {}/{}".format(self.N_low_fidelity,self.MAX_low_fidelity))

        self.X_L = np.vstack((self.X_L, X_next))  # update x_l with new points
        # Y_next = 1.0*self.sampling_func_L(self.X_next[None,:])  # runs meta_low_fidelity on x_next
        self.Y_L = np.concatenate((self.Y_L, np.array(Y_next)))  # update y_l with new evaluations
        self.N_L += 1
        # self.exp_result_array.append(Y_next[0])
        # rel_snap = 1.0*self.sampling_func_L(X_next[None,:])
        self.rel_snap_array.append(Y_next[0])
        # print("rel_snap: {}".format(rel_snap[0]))

        # print("N_L: {}".format(self.N_L))
        
        # if self.X_cand.shape[0] < self.N_cand:
        #     print("Remaining X_cand: {}".format(self.X_cand.shape[0]))
        #     self.X_cand = np.append(self.X_cand, self.sample_data(self.N_cand-self.X_cand.shape[0]),0)
        # print("-------------------------------------------")
    
    def save_result_data(self, filedir, filename_result):
        yamlFile = os.path.join(filedir, filename_result)
        yaml_out = open(yamlFile,"w")
        high_idx = 0
        low_idx = 0
        for it in range(len(self.min_time_array)):
            if self.fidelity_array[it] == 1:
                yaml_out.write("iter{}:\n".format(high_idx))
                high_idx += 1
                low_idx = 0
            else:
                yaml_out.write("iter{}_{}:\n".format(high_idx-1,low_idx))
                low_idx += 1
            yaml_out.write("  found_ei: {}\n".format(self.found_ei_array[it]))
            yaml_out.write("  exp_result: {}\n".format(self.exp_result_array[it]))
            yaml_out.write("  rel_snap: {}\n".format(self.rel_snap_array[it]))
            yaml_out.write("  min_time: {}\n".format(self.min_time_array[it]))
            yaml_out.write("  alpha_cand: [{}]\n\n".format(','.join([str(x) for x in self.alpha_cand_array[it]])))
        yaml_out.close()

    ### LINES 5-7 IN ALGORITHM 1 ###
    def active_learning(self, N=15, MAX_low_fidelity=20, plot=False, filedir='./mfbo_data', \
                        filename_plot='active_learning_%i.png', \
                        filename_result='result.yaml', \
                        filename_exp='exp_data.yaml'):
        
        if not hasattr(self, 'start_iter'):
            self.start_iter = 0
            self.min_time_array = []
            self.alpha_cand_array = []
            self.fidelity_array = []
            self.found_ei_array = []
            self.exp_result_array = []
            self.rel_snap_array = []
            self.min_time_array.append(self.min_time)
            self.alpha_cand_array.append(self.alpha_min_cand)
            self.exp_result_array.append(1)
            self.rel_snap_array.append(1)
            self.fidelity_array.append(1)
            self.found_ei_array.append(1)
            self.writer.add_scalar('/min_time', 1.0, 0)
            self.writer.add_scalar('/num_low_fidelity', 0, 0)
            self.writer.add_scalar('/num_found_ei', 0, 0)
            self.writer.add_scalar('/num_failure', 0, 0)
            self.writer.add_scalar('/rel_snap', 1.0, 0)
        
        self.MAX_low_fidelity = MAX_low_fidelity
        main_iter_start = self.start_iter
        self.min_time = self.min_time_array[-1]
        
        # Save results if the starting iteration is the last one
        if main_iter_start == N-1:
            self.save_result_data(filedir, filename_result)
        # Main loop for active learning iterations
        
        for main_iter in range(main_iter_start, N):
            prGreen("#################################################")
            print('%i / %i' % (main_iter+1,N))
            self.X_next_fidelity = 0
            if not hasattr(self, 'N_low_fidelity'):
                self.N_low_fidelity = 0
            # If the next point is not found, create a model and compute the next point
            num_found_ei = 0
                    # Create a model
            num_low_fidelity = self.N_low_fidelity
                    # Compute the next point

            self.create_model(num_epochs=self.iter_create_model)  # create model with current dataset
            self.compute_next_point_cand()  # get x from acquisition function
            self.append_next_point()  # append point to dataset

            self.min_time_array.append(self.min_time)
            self.alpha_cand_array.append(self.alpha_min_cand)
            self.fidelity_array.append(self.X_next_fidelity)
            if self.flag_found_ei:
                # add an expected improvement is found, append 1 to ei array
                self.found_ei_array.append(1)
                num_found_ei += 1
            else:
                self.found_ei_array.append(0)
            num_low_fidelity += 1
            
            self.start_iter = main_iter
            self.save_exp_data(filedir, filename_exp)

            num_failure = 0
            for it in range(len(self.min_time_array)):
                if self.fidelity_array[it] == 1 and self.exp_result_array[it] == 0:
                    num_failure += 1
            self.writer.add_scalar('/min_time', self.min_time, main_iter+1)
            self.writer.add_scalar('/num_low_fidelity', num_low_fidelity, main_iter+1)
            self.writer.add_scalar('/num_found_ei', num_found_ei, main_iter+1)
            self.writer.add_scalar('/num_failure', num_failure, main_iter+1)
            
            min_time_idx = 0
            for it in range(len(self.min_time_array)):
                if self.fidelity_array[it] == 1 and self.min_time_array[it] == self.min_time:
                    min_time_idx = it
                    break
            self.writer.add_scalar('/rel_snap', self.rel_snap_array[min_time_idx], main_iter+1) #TODO fix error here, snap array is literally [1], need to debug
            # TODO: snap is dependent upon the high fideltit sampling funciton, do i just swtich to low?
            self.save_result_data(filedir, filename_result)
        return

    def plot(self, filename='MFBO_2D.png'):
        # assert self.dim == 2
        print("shape of xl",self.X_L.shape)
        mean, var, prob_cand, _  = self.forward_cand()
        
        ent_cand = -np.abs(mean)/(var + 1e-9)

        fig = plt.figure(1,figsize=(9,7.5))
        plt.clf()
        ax = plt.subplot(111)
        
        X_test_denorm_ = np.repeat(np.expand_dims(self.lb_i,0),self.X_test.shape[0],axis=0) + np.multiply(self.X_test, np.repeat(np.expand_dims(self.ub_i-self.lb_i,0),self.X_test.shape[0],axis=0))
        X_test_time_ = np.multiply(X_test_denorm_, np.repeat(np.expand_dims(self.t_set_sim,0),self.X_test.shape[0],axis=0))
        cnt = plt.tricontourf(X_test_time_[:,0], X_test_time_[:,1], prob_cand, np.linspace(-0.01,1.01,100),cmap='coolwarm_r',alpha=0.4)
        for c in cnt.collections:
            c.set_edgecolor("face")

        cb = plt.colorbar(ticks = [0,1])
        cb.set_label('feasibility (y)', labelpad=0, fontsize='xx-large')

        cb.ax.tick_params(labelsize='xx-large')
        labels = cb.ax.get_yticklabels()
        labels[0].set_verticalalignment("bottom")
        labels[-1].set_verticalalignment("top")

        X_L_denorm_ = np.repeat(np.expand_dims(self.lb_i,0),self.X_L.shape[0],axis=0) \
            + np.multiply(self.X_L,np.repeat(np.expand_dims(self.ub_i-self.lb_i,0),self.X_L.shape[0],axis=0))
        X_L_time_ = np.multiply(X_L_denorm_, np.repeat(np.expand_dims(self.t_set_sim,0),X_L_denorm_.shape[0],axis=0))
        plt.scatter(X_L_time_[:,0], X_L_time_[:,1],c=self.Y_L[:],cmap='coolwarm_r', \
                    marker='x',s=50,label='low fidelity sample')

        # X_H_denorm_ = np.repeat(np.expand_dims(self.lb_i,0),self.X_H.shape[0],axis=0) + np.multiply(self.X_H, np.repeat(np.expand_dims(self.ub_i-self.lb_i,0),self.X_H.shape[0],axis=0))
        # X_H_time_ = np.multiply(X_H_denorm_, np.repeat(np.expand_dims(self.t_set_sim,0),X_H_denorm_.shape[0],axis=0))
        # plt.scatter(X_H_time_[:,0], X_H_time_[:,1],c=self.Y_H[:],cmap='coolwarm_r',\
        #             s=150,edgecolors='k',label='high fidelity sample')
        
        # X_best_denorm_ = lb_i + np.multiply(X_best, ub_i-lb_i)
        X_best_time_ = np.multiply(self.alpha_min, self.t_set_sim)
        plt.scatter([X_best_time_[0]],[X_best_time_[1]],color='lawngreen', \
                    marker='*',edgecolors='k',s=400,label='current best solution')
        
#         for k in range(self.N_H-self.N_H_i):
#             plt.text(X_H_time_[self.N_H_i+k,0], X_H_time_[self.N_H_i+k,1], str(k+1), fontsize=12, color='green')
        plt.legend()
        
        X_next_denorm_ = self.lb_i + np.multiply(self.X_next, self.ub_i-self.lb_i)
        X_next_time_ = np.multiply(X_next_denorm_, self.t_set_sim)
        
        plt.scatter([X_next_time_[0]],[X_next_time_[1]],color = 'k',marker = '*')
        plt.legend(loc=4, fontsize='xx-large')
        plt.xlim([self.lb_i[0]*self.t_set_sim[0],self.ub_i[0]*self.t_set_sim[0]])
        plt.ylim([self.lb_i[1]*self.t_set_sim[1],self.ub_i[1]*self.t_set_sim[1]])
        
        ax.xaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        
        def prettyplot_tmp(xlabel, ylabel, xlabelpad = -10, ylabelpad = -20, minXticks = True, minYticks = True):
            plt.xlabel(xlabel, labelpad = xlabelpad, fontsize='xx-large')
            plt.ylabel(ylabel, labelpad = ylabelpad, fontsize='xx-large')

            if minXticks:
                plt.xticks(plt.xlim(), fontsize='xx-large')
                rang, labels = plt.xticks()
                labels[0].set_horizontalalignment("left")
                labels[-1].set_horizontalalignment("right")

            if minYticks:
                plt.yticks(plt.ylim(), fontsize='xx-large')
                rang, labels = plt.yticks()
                labels[0].set_verticalalignment("bottom")
                labels[-1].set_verticalalignment("top")

        prettyplot_tmp("$\mathregular{x_1}$ [s]", "$\mathregular{x_2}$ [s]", ylabelpad=-15)
        
        plt.tight_layout()
        plt.savefig(filename)
        plt.close()

class ActiveMFDGP(MFBOAgentBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.min_loss = -1
        self.batch_size = kwargs.get('gpu_batch_size', 256)

    def create_model(self, num_epochs=500):
        self.train_x_L = torch.tensor(self.X_L).float().to(device)
        self.train_y_L = torch.tensor(self.Y_L).float().to(device)
        self.train_dataset_L = TensorDataset(self.train_x_L, self.train_y_L)
        self.train_loader_L = DataLoader(self.train_dataset_L, batch_size=self.batch_size, shuffle=True)

        train_x = [self.train_x_L]
        train_y = [self.train_y_L]
        
        if not hasattr(self, 'clf'):
            self.clf = MFDeepGPC(train_x, train_y, num_inducing=100).to(device) # CHANGED was 128

        optimizer = torch.optim.Adam([
            {'params': self.clf.parameters()},
        ], lr=0.001)
        mll = VariationalELBO(self.clf.likelihood, self.clf, self.train_x_L.shape[-2])
        start_time = time.time()
        N_data = self.X_L.shape[0]

        train_loss = []
        test_loss = []

        with gpytorch.settings.fast_computations(log_prob=False, solves=False):
            for i in range(num_epochs):
                avg_loss = 0
                for minibatch_i, (x_batch, y_batch) in enumerate(self.train_loader_L):
                    optimizer.zero_grad()
                    output = self.clf(x_batch, fidelity=1)
                    loss = -mll(output, y_batch)
                    loss.backward(retain_graph=True)
                    avg_loss += loss.item()/N_data
                    optimizer.step()
                
                train_loss.append(avg_loss)

                with torch.no_grad():
                    self.X_cand = self.sample_data(self.N_cand)
                    test_x = torch.tensor(self.X_cand).float().to(device)
                    test_dataset = TensorDataset(test_x)
                    test_loader = DataLoader(test_dataset, batch_size=self.batch_size, shuffle=False)
                    # test_output = self.clf(self.X_test, fidelity=1)
                    # test_loss = -mll(test_output, self.test).item()
                    # test_loss.append(test_loss)

                # for minibatch_i, (x_batch, y_batch) in enumerate(self.train_loader_H):
                #     optimizer.zero_grad()
                #     output = self.clf(x_batch, fidelity=2)
                #     loss = -mll(output, y_batch)
                #     output_L = self.clf(x_batch, fidelity=1)
                #     loss -= mll(output_L, y_batch)
                #     avg_loss += loss.item()/N_data
                #     loss.backward(retain_graph=True)
                #     optimizer.step()

                if (i+1)%20 == 0 or i == 0:
                    print('Epoch %d/%d - Loss: %.3f' % (i+1, num_epochs, avg_loss))
                
                if self.min_loss > avg_loss and (i+1) >= 20:
                    print('Early stopped at Epoch %d/%d - Loss: %.3f' % (i+1, num_epochs, avg_loss))
                    break
        
        if self.min_loss < 0:
            self.min_loss = avg_loss
        
        print(" - Time: %.3f" % (time.time() - start_time))

         # Plot the loss history
        plt.figure(figsize=(8, 5))
        plt.plot(range(1, len(train_loss) + 1), train_loss, label="Training Loss")
        plt.xlabel("Epochs")
        plt.ylabel("Loss")
        plt.title("Training Loss Over Epochs")
        plt.legend()
        plt.grid()
        plt.savefig(os.path.join(os.getcwd(),"loss_plot.png"))
    

    # get mean, standard deviation from P(f|x_i, D_i)
    def forward_cand(self):
        self.X_cand = self.sample_data(self.N_cand)  # XF = randomly sampled NS points
        if self.sampling_mode >= 2:
            self.X_cand[:,:self.t_dim] = np.multiply(self.X_cand[:,:self.t_dim], \
                                       np.repeat(np.expand_dims(self.alpha_min[:self.t_dim],0),self.X_cand.shape[0],axis=0))
            self.X_cand[:,:self.t_dim] += self.lb_i/(self.ub_i-self.lb_i)*(self.alpha_min[:self.t_dim]-1)
            self.X_cand = self.X_cand[(np.min(self.X_cand[:,:self.t_dim]-self.lb_i,axis=1)>=0) \
                                                     & (np.max(self.X_cand[:,:self.t_dim]-self.ub_i,axis=1)<=0),:]
        
        test_x = torch.tensor(self.X_cand).float().to(device)
        test_dataset = TensorDataset(test_x)
        test_loader = DataLoader(test_dataset, batch_size=self.batch_size, shuffle=False)

        mean_L = np.empty(0)
        var_L = np.empty(0)
        prob_cand_L = np.empty(0)
        prob_cand_L_mean = np.empty(0)
        
        for minibatch_i, (x_batch,) in enumerate(test_loader): #TODO
            p, m, v, pm = self.clf.predict_proba_MF(x_batch, fidelity=1, C_L=self.C_L, beta=self.beta, return_all=True)

            mean_L = np.append(mean_L, m)  # means
            var_L = np.append(var_L, v)  # stdevs
            prob_cand_L = np.append(prob_cand_L, p[:,1])  # bernoulli likelihoods
            prob_cand_L_mean = np.append(prob_cand_L_mean, pm[:,1])
        
        return mean_L, var_L, prob_cand_L, prob_cand_L_mean
    
    def predict_single_point(self, X):
        X_tensor = torch.tensor(X).float().to(device)
        with torch.no_grad():
            prob, mean, var, prob_mean = self.clf.predict_proba_MF(X_tensor, fidelity=1, C_L=self.C_L, beta=self.beta, return_all=True)
        return mean, var, prob[:, 1]

class TwoDrone():
    def __init__(self, **kwargs):
        self.drone_1 = ActiveMFDGP(X_L = kwargs.get("X1"),
                                   Y_L = kwargs.get("Y1"),
                                   t_set_sim = kwargs.get("t_set_sim_1"),
                                   lb_i = kwargs.get("lb_i"),
                                   ub_i = kwargs.get("ub_i"),
                                   rand_seed=kwargs.get("rand_seed", None),
                                   beta = kwargs.get("beta"),
                                   N_cand = kwargs.get("N_cand"),
                                   batch_size = kwargs.get("batch_size"),
                                   model_prefix = kwargs.get("model_prefix"),
                                   sampling_mode=0,
                                   )
        self.drone_2 = ActiveMFDGP(X_L = kwargs.get("X2"),
                                   Y_L = kwargs.get("Y2"),
                                   t_set_sim = kwargs.get("t_set_sim_2"),
                                   lb_i = kwargs.get("lb_i"),
                                   ub_i = kwargs.get("ub_i"),
                                   rand_seed=kwargs.get("rand_seed", None),
                                   beta = kwargs.get("beta"),
                                   N_cand = kwargs.get("N_cand"),
                                   batch_size = kwargs.get("batch_size"),
                                   model_prefix = kwargs.get("model_prefix"),
                                   sampling_mode=0)
        self.drone_12 = ActiveMFDGP(X_L = kwargs.get("X12"),
                                    Y_L = kwargs.get("Y12"),
                                    t_set_sim = np.concatenate((kwargs.get("t_set_sim_1"), kwargs.get("t_set_sim_2"))),
                                    lb_i = kwargs.get("lb_i"),
                                    ub_i = kwargs.get("ub_i"),
                                    rand_seed=kwargs.get("rand_seed", None),
                                    beta = kwargs.get("beta"),
                                    N_cand = kwargs.get("N_cand"),
                                    batch_size = kwargs.get("batch_size"),
                                    model_prefix = kwargs.get("model_prefix"),
                                    sampling_mode=0)

        self.min_time = 1  # ??
        self.t_set_sta = kwargs.get("t_set_sta")
        
        self.drone_1_cols = range(4)
        self.drone_2_cols = range(4, 8)

        # variables defined in IV.a
        self.h = 0.001
        self.lb = kwargs.get("lb_i")[0]
        self.ub = kwargs.get("ub_i")[0]

        self.X = None  # sample set X to search over with acquisition function
        self.X_next = None  # selected sample (output from acquisition)

        # stuff
        self.eval_func_1 = kwargs.get("eval_func_1")  # meta_low_fidelity
        self.eval_func_2 = kwargs.get("eval_func_2")
        self.eval_func_12 = kwargs.get("eval_func_12")  # meta_low_fidelity_multi

        self.N_s = 2 # Size of candidate data points
        self.N_1 = 5 # Number of low fidelity samples to generate for a single drone to evaluate feasibility
        self.N_2 = 128 # Number of samples needed for acquisition function
        self.C_1 = 0.7 # Threshold for dynamic feasibility
        self.C_2 = 0.7 # Threshold for collision feasibility

    def compute_next_point_cand(self):
        mean_1, var_1, prob_cand_1, _ = self.drone_1.forward_cand()
        mean_2, var_2, prob_cand_2, _ = self.drone_2.forward_cand()
        mean_12, var_12, prob_cand_12, _ = self.drone_12.forward_cand()

        # eq 21
        alpha_explore = -(np.abs(mean_1)/(var_1 + 1e-9) + np.abs(mean_2)/(var_2 + 1e-9)) - np.abs(mean_12)/(var_12 + 1e-9)
        
        # find alpha_exploit following mfbo eq. 19
        # self.flag_found_ei = False
        max_alpha_exploit_idx = -1  # -1 if alpha_exploit not updated
        alpha_exploit = 0  # alpha_exploit initially set to 0
        min_time_tmp = self.min_time  # xbar
        # check if there exists x in X where alpha_exploit(x) > 0
        for it in range(self.X.shape[0]):
            # DRONE 1
            x_cand_denorm = self.lb + (self.ub - self.lb) * self.X[it, self.drone_1_cols]
            min_time_drone_1 = x_cand_denorm.dot(self.drone_1.t_set_sim)/np.sum(self.drone_1.t_set_sim)
            # DRONE 2
            x_cand_denorm = self.lb + (self.ub - self.lb) * self.X[it, self.drone_2_cols]
            min_time_drone_2 = x_cand_denorm.dot(self.drone_2.t_set_sim)/np.sum(self.drone_2.t_set_sim)
            # ALPHA_EI
            alpha_ei = max(self.drone_1.min_time, self.drone_2.min_time) - max(min_time_drone_1, min_time_drone_2)

            # ~P(y=1|x)
            prob_cand = (prob_cand_1[it] * prob_cand_2[it]) * prob_cand_12[it]
            
            # GET ALPHA_EXPLOIT FROM CONDITION ON PROB_CAND
            alpha_exploit_tmp = alpha_ei * prob_cand
            if alpha_exploit_tmp > alpha_exploit and prob_cand > self.h:
                alpha_exploit = alpha_exploit_tmp
                max_alpha_exploit_idx = it

        # alpha_exploit
        if max_alpha_exploit_idx != -1:
            X_next = self.X[max_alpha_exploit_idx,:]  # select x from X
            print(f"alpha_exploit: {alpha_exploit}")
        # alpha_explore
        else:
            X_next = self.X[alpha_explore.argmax(),:]
            print(f"alpha_explore: {alpha_explore}")
        # set min time based on upper and lower bounds
        x_denorm_1 = self.lb + (self.ub - self.lb) * X_next[self.drone_1_cols]
        # self.drone_1.lb_i + np.multiply(self.X_next[:self.drone_1_cols], self.drone_1.ub_i - self.drone_1.lb_i)
        self.drone_1.min_time = x_denorm_1.dot(self.drone_1.t_set_sim)
        x_denorm_2 = self.lb + (self.ub - self.lb) * X_next[self.drone_2_cols]
        # self.drone_2.lb_i + np.multiply(self.X_next[:self.drone_2_cols], self.drone_2.ub_i - self.drone_2.lb_i)
        self.drone_2.min_time = x_denorm_2.dot(self.drone_2.t_set_sim)
        
        # self.alpha_min_cand = self.lb_i + np.multiply(self.X_next, self.ub_i-self.lb_i)
        return X_next.reshape(1, -1)

    def evaluate_x_next(self, X_next):
        Y_next = np.array([0.0])
        Y_next_1 = 1.0 * self.eval_func_1(X_next[:, self.drone_1_cols]) # size np.array((1,))
        if Y_next_1 == np.array([1.0]):
            Y_next_2 = 1.0 * self.eval_func_2(X_next[:, self.drone_2_cols])
            if Y_next_2 == np.array([1.0]):
                Y_next_12 = self.eval_func_12(X_next[:, self.drone_1_cols], X_next[:, self.drone_2_cols])
                if Y_next_12 == np.array([1.0]):
                    Y_next = np.array([1.0])
        return Y_next
    
    def update_datasets(self, X_next, Y_next):
        self.drone_1.append_next_point(X_next[:, self.drone_1_cols], Y_next)
        self.drone_1.append_next_point(X_next[:, self.drone_2_cols], Y_next)
        self.drone_12.append_next_point(X_next, Y_next)
    
    def update_models(self, iters=200):
        self.drone_1.create_model(num_epochs=iters)
        self.drone_2.create_model(num_epochs=iters)
        self.drone_12.create_model(num_epochs=iters)

    def bayes_opt(self, min_iters=10, max_iters=100):
        self.update_models(iters=500)
        print(self.drone_12.predict_single_point(self.drone_12.X_L[(self.drone_12.Y_L == 1).T])[2])
        print(self.drone_12.predict_single_point(self.drone_12.X_L[(self.drone_12.Y_L == 0).T])[2])
        # print(self.drone_1.forward_cand()[2])
        for it in range(max_iters):
            print(f"iteration number {it}")
            self.X = self.get_dataset()
            X_next = self.compute_next_point_cand()
            Y_next = self.evaluate_x_next(X_next)
            print(f"{X_next} {Y_next}")
            self.update_datasets(X_next, Y_next)

            if it >= min_iters-1 and Y_next[0] == 1:
                return X_next, Y_next
    
    def _scale_arr(self, X_t, X_F):
        X_t_copy = X_t.copy()
        # X_t_copy[:, 0] = (X_t[:, 0]/(X_t[:, 0] + X_t[:, 1])) * (X_F[:, 0] + X_F[:, 1])
        # X_t_copy[:, 1] = (X_t[:, 1]/(X_t[:, 0] + X_t[:, 1])) * (X_F[:, 0] + X_F[:, 1])
        # X_t_copy[:, 2] = (X_t[:, 2]/(X_t[:, 2] + X_t[:, 3])) * (X_F[:, 2] + X_F[:, 3])
        # X_t_copy[:, 3] = (X_t[:, 3]/(X_t[:, 2] + X_t[:, 3])) * (X_F[:, 2] + X_F[:, 3])
        time12 = X_F[0, 0] * self.t_set_sta[0] + X_F[0, 1] * self.t_set_sta[1]
        time34 = X_F[0, 2] * self.t_set_sta[2] + X_F[0, 3] * self.t_set_sta[3]
        # X_t12 = np.sum(X_t[:, 0:2], axis=1)
        # X_t34 = np.sum(X_t[:, 3:4], axis=1)
        # X_t_copy[:, 0] = (time12 - (self.t_set_sta[1] * X_t12)) / (self.t_set_sta[0] - self.t_set_sta[1])
        # X_t_copy[:, 1] = (time12 - (self.t_set_sta[0] * X_t12)) / (self.t_set_sta[1] - self.t_set_sta[0])
        # X_t_copy[:, 2] = (time34 - (self.t_set_sta[3] * X_t34)) / (self.t_set_sta[2] - self.t_set_sta[3])
        # X_t_copy[:, 3] = (time34 - (self.t_set_sta[2] * X_t34)) / (self.t_set_sta[3] - self.t_set_sta[2])
        X_t12 = (X_t[:, 0]/X_t[:,1])
        X_t34 = X_t[:, 2]/X_t[:,3]
        X_t_copy[:, 0] = (time12) / (self.t_set_sta[0] + (self.t_set_sta[1]/X_t12))
        X_t_copy[:, 1] = (time12) / (self.t_set_sta[1] + (self.t_set_sta[0]*X_t12))
        X_t_copy[:, 2] = (time34) / (self.t_set_sta[2] + (self.t_set_sta[3]/X_t34))
        X_t_copy[:, 3] = (time34) / (self.t_set_sta[3] + (self.t_set_sta[2]*X_t34))
        return X_t_copy
        
    def sample_traj(self, drone, X_F):
        # x = np.empty((self.N_1, drone.dim))
        X = np.empty((0, drone.dim))
        C1_tmp = self.C_1
        while X.shape[0] < self.N_1:
            X_t_copy = np.empty((0, drone.X_L.shape[1]))
            while X_t_copy.shape[0] < self.N_s:
                X_t = drone.sample_data(self.N_s)
                scaled = self._scale_arr(X_t, X_F)
                X_t_copy = np.vstack([X_t_copy, scaled[np.all(scaled >= 0, axis=1), :]])
            # Rescale X_t with X_F
            
            # X_t = drone.lb_i + np.multiply(X_t, drone.ub_i-drone.lb_i)
            # print(drone.predict_single_point(X_t)[2])
            valid_rows = drone.predict_single_point(X_t_copy)[2] > C1_tmp
            X = np.vstack([X, X_t_copy[valid_rows.T, :]])
            if sum(valid_rows) == 0:
                C1_tmp -= 0.01
        if C1_tmp == self.C_1 and self.C_1 < 0.8:
            self.C_1 += 0.01
        return X[:self.N_1, :]

    def get_dataset(self):
        # X_F = self.drone_1.sample_data(self.N_s)
        X_F = self.drone_1.sample_data(1)
        X_F = np.vstack([X_F]*self.N_s)
        X = np.empty((0,self.drone_1.dim + self.drone_2.dim))
        # print(X_F)
        C2_tmp = self.C_2
        while X.shape[0] < self.N_2:
            # print("drone_12")
            X_t_1 = self.sample_traj(self.drone_1, X_F)
            X_t_2 = self.sample_traj(self.drone_2, X_F)
            x = np.hstack([X_t_1, X_t_2])
            valid_rows = self.drone_12.predict_single_point(x)[2] > C2_tmp
            # print(self.drone_12.predict_single_point(x)[2])
            X = np.vstack([X, x[valid_rows.T, :]])
            # print(len(X))
            if sum(valid_rows) == 0:
                C2_tmp -= 0.01
            # print(C2_tmp)
        if C2_tmp == self.C_2 and self.C_2 < 0.8:
            self.C_2 += 0.01
            # for x1 in X_t_1:
            #     for x2 in X_t_2:
            #         x = np.hstack([x1, x2])
            #         print("drone12")
            #         print(self.drone_12.predict_single_point(x)[2])
            #         if self.drone_12.predict_single_point(x)[2] > self.C_2:
            #             print("Success")
            #             X = np.vstack([X, x])
        print(self.C_2)
        return X[:self.N_2, :]
