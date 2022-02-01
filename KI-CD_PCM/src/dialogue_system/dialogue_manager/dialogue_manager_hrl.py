# -*- coding:utf-8 -*-


import copy
import random
import pickle
import numpy as np
from collections import deque
import sys, os
import json


sys.path.append(os.getcwd().replace("src/dialogue_system/dialogue_manager",""))

from src.dialogue_system.state_tracker import StateTracker as StateTracker
from src.dialogue_system import dialogue_configuration
from src.dialogue_system.disease_classifier import dl_classifier

import numpy as np
from sklearn import svm
import pickle
class DialogueManager_HRL(object):
    """
    Dialogue manager of this dialogue system.
    """
    def __init__(self, user, agent, parameter):
        self.state_tracker = StateTracker(user=user, agent=agent, parameter=parameter)
        self.parameter = parameter
        self.GTD = pickle.load(open('Data/GDTop5.p', 'rb')) #Top 5 symptoms
        self.GS = pickle.load(open('Data/DSS.p', 'rb')) #Grp wise symptom
        self.experience_replay_pool = deque(maxlen=self.parameter.get("experience_replay_pool_size"))
        self.inform_wrong_disease_count = 0
        self.dialogue_output_file = parameter.get("dialogue_file")
        self.save_dialogue = parameter.get("save_dialogue")
        self.action_history = []
        self.master_action_history = []
        self.lower_action_history = []
        self.group_id_match = 0
        self.p = 0  ##Penalty for not asking probable slots
        #self.hc = 0 #both current grp and prediction belongs to dieases
        #self.pre_grp = 1
        #self.pd = -1
        #self.pdp = 0
        self.repeated_action_count = 0
        self.slot_set = pickle.load(open(self.parameter.get("slot_set"), 'rb'))
        self.GA = pickle.load(open(self.parameter.get("GA"), 'rb')) #Group wise symptom
        self.disease_symptom = pickle.load(open(self.parameter.get("disease_symptom"),'rb'))
        self.dsg = pickle.load(open(self.parameter.get("dsg"),'rb')) #Disease Symptom


        self.slot_set.pop('disease')
        self.grp = [1,4,5,6,7,12,13,14,19]
        self.id2disease = {}
        self.disease2id = {}
        for disease, v in self.disease_symptom.items():
            self.id2disease[v['index']] = disease
            self.disease2id[disease] = v['index']
        self.disease_replay = deque(maxlen=10000)
        self.worker_right_inform_num = 0
        self.acc_by_group = {x:[0,0,0] for x in ['12', '13', '14', '19', '1', '4', '5', '6', '7']}
        # 这里的三维向量分别表示inform的症状正确的个数、group匹配正确的个数、隶属于某个group的个数


        if self.parameter.get("train_mode")==False:
            self.test_by_group = {x:[0,0,0] for x in ['12', '13', '14', '19', '1', '4', '5', '6', '7']}
            #这里的三维向量分别表示成功次数、group匹配正确的个数、隶属于某个group的个数
            self.disease_record = []
            self.lower_reward_by_group = {x: [] for x in ['12', '13', '14', '19', '1', '4', '5', '6', '7']}
            # self.master_index_by_group = {x:[] for x in ['12', '13', '14', '19', '1', '4', '5', '6', '7']}
            self.master_index_by_group = []
            self.symptom_by_group = {x: [0,0] for x in ['12', '13', '14', '19', '1', '4', '5', '6', '7']}

    def next(self, greedy_strategy, save_record, index):
        """
        The next two turn of this dialogue session. The agent will take action first and then followed by user simulator.
        :param save_record: bool, save record?
        :param train_mode: bool, True: the purpose of simulation is to train the model, False: just for simulation and the
                           parameters of the model will not be updated.
        :return: immediate reward for taking this agent action.
        """
        # Agent takes action.
        lower_reward = 0
        self.p = 0
        #self.hc = 0
        gamma = self.parameter.get("gamma")
        #probable_slot = []
        probable_sym = []
        state = self.state_tracker.get_state()
        state_rep = self.current_state_representation(state)
        Ys, pre_disease = self.model.predict([state_rep])
        kk = Ys.data.cpu().numpy()[0]
        diease_prob = max(kk)

        if(diease_prob>0.5):
            did = pre_disease[0]
            d = self.id2disease[did]
            for j in range(0,9):
                if d in list(self.GTD[self.grp[j]].keys()):
                    break;
            print('Current Probable Diease: ',d)
            print('Diease probability: ',diease_prob)
            probable_sym = list(self.disease_symptom[d]['symptom'])[0:5]
            print('Probable Symptom : ',probable_sym)
            imp_sym = self.Dtop5Sym(probable_sym)
            print('Probable Symptom encoding:',imp_sym)
            self.state_tracker.ProbableSymUpdate(j, did,diease_prob,imp_sym)
        state = self.state_tracker.get_state()
        group_id = self.state_tracker.user.goal["group_id"]

        if self.parameter.get("agent_id")=='agentdqn':
            self.master_action_space = self.state_tracker.agent.action_space
        else:
            self.master_action_space = self.state_tracker.agent.master_action_space
        #if self.state_tracker.agent.subtask_terminal:
        #    self.master_state = copy.deepcopy(state)
        if self.parameter.get("agent_id") == 'agentdqn':
            master_action_index = len(self.master_action_space)
            agent_action,  lower_action_index = self.state_tracker.agent.next(state=state, turn=self.state_tracker.turn,
                                                                                            greedy_strategy=greedy_strategy,
                                                                        index=index)
            #print('Agent Action:',agent_action)
            #print('Lower_action_index:',lower_action_index)
        else:
            agent_action, master_action_index, lower_action_index = self.state_tracker.agent.next(state=state, turn=self.state_tracker.turn,greedy_strategy=greedy_strategy, index=index)
            if len(list(agent_action["request_slots"].keys())):
                lower_action = list(agent_action["request_slots"].keys())[0]
                if lower_action in self.lower_action_history:
                    self.state_tracker.repeatition()

            print('Master action index:',master_action_index)
            print('Lower action index: ',lower_action_index)
            print('Agent Action:',agent_action)
        if len(agent_action["request_slots"]) > 0:
            assert len(list(agent_action["request_slots"].keys())) == 1
            action_type = "symptom"
        elif len(agent_action["inform_slots"]) > 0:
            lower_action = list(agent_action["inform_slots"].keys())[0]
            assert len(list(agent_action["inform_slots"].keys()))==1
            action_type = "disease"
            #print("#########")
        else:
            lower_action = "return to master"
            assert agent_action["action"] == 'return'
            action_type = 'return'

        if self.parameter.get("disease_as_action")==False:
            if self.parameter.get("train_mode") == True:
                condition = False
            else:
                condition = state['turn']==self.parameter.get("max_turn")+16
            if action_type == "disease" or condition:# or lower_action in self.lower_action_history:
                #once the action is repeated or the dialogue reach the max turn, then the classifier will output the predicted disease
                state_rep = self.current_state_representation(state)
                #state_GG = self.current_state_representation_G(state,int(group_id))
                disease = self.state_tracker.user.goal["disease_tag"]
                Ys, pre_disease = self.model.predict([state_rep])
                Ys = Ys.data.cpu().numpy()[0]
                d_p = max(Ys)


                did = pre_disease[0]

                print('Predicted disease:',self.id2disease[did],'with probability',d_p)
                '''
                if index>20:
                    print(Ys)
                    temp = self.exp_transform(Ys.detach().cpu().numpy()[0])
                    print(max(temp), temp)
                '''
                #print(self.id2disease[pre_disease[0]])
                self.disease_replay.append((state_rep, self.disease2id[disease]))

                lower_action_index = -1
                master_action_index = len(self.master_action_space)
                agent_action = {'action': 'inform', 'inform_slots': {"disease":self.id2disease[did]}, 'request_slots': {},"explicit_inform_slots":{}, "implicit_inform_slots":{}}
                if self.parameter.get("train_mode") == False:
                    self.disease_record.append([disease,self.id2disease[did]])

        #if master_action_index==9:
        #    print(master_action_index)  这里也存在9
        self.state_tracker.state_updater(agent_action=agent_action)
        # print("turn:%2d, state for agent:\n" % (state["turn"]) , json.dumps(state))

        # User takes action.
        user_action, reward, episode_over, dialogue_status = self.state_tracker.user.next(agent_action=agent_action,turn=self.state_tracker.turn)
        print('User Action:',user_action)
        print('dialogue status:',dialogue_status)
        if len(agent_action["request_slots"]) > 0:
            lower_action = list(agent_action["request_slots"].keys())[0]
            if len(probable_sym) and lower_action in probable_sym:
                self.p = 1
            elif len(probable_sym) and (list(user_action['inform_slots'].values())[0] != True) and lower_action not in probable_sym:
                self.p = -1
        self.state_tracker.state_updater(user_action=user_action)


        if(diease_prob>0.80):
            if master_action_index!=9:
                reward =reward - gamma
            else:
                reward = reward + gamma
        if(diease_prob<0.65 and user_action['turn']<14): ##To avoid early prediction
            if master_action_index==9:
                reward =reward - 3*gamma


        if master_action_index<9:
            if diease_prob>0.3 and user_action['turn']<3:
                reward = reward + 2*gamma



        # if self.state_tracker.turn == self.state_tracker.max_turn:
        #     episode_over = True

        if master_action_index < len(self.master_action_space):
            self.master_action_history.append(self.master_action_space[master_action_index])

        if  self.parameter.get("initial_symptom"):
            #print(master_action_index)
            if self.master_action_space[master_action_index] == group_id and self.state_tracker.get_state()["turn"]==2:
                reward = self.parameter.get("reward_for_success")
                self.group_id_match += 1
            elif episode_over == True:
                reward = reward/2
            else:
                reward = 0
        elif self.parameter.get("use_all_labels") and episode_over==True:
            #if self.master_action_space[master_action_index] == group_id and action_type == "disease":
            #    reward = self.parameter.get("reward_for_success")
            #else:
            #    reward = 0
            if self.parameter.get("agent_id").lower() == "agenthrlnew" and self.parameter.get("disease_as_action")==True:
                if self.master_action_space[master_action_index] == group_id and action_type == "disease" and reward!=self.parameter.get("reward_for_success"):
                    reward = self.parameter.get("reward_for_success")/2
            elif self.parameter.get("agent_id").lower() == "agenthrlnew" and self.parameter.get("disease_as_action") == False:
                if action_type=="disease" and master_action_index<len(self.master_action_space):
                    reward = self.parameter.get("reward_for_repeated_action")
                    self.repeated_action_count +=1
                    dialogue_status = dialogue_configuration.DIALOGUE_STATUS_FAILED
                    #print("#############")

            elif self.parameter.get("agent_id").lower() == "agenthrlnew2":
                if action_type=="disease" and master_action_index<len(self.master_action_space):
                    reward = self.parameter.get("reward_for_repeated_action")
                    self.repeated_action_count +=1


                    #reward = -33
                    #print("$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$")

        if action_type=="symptom"  and self.parameter.get("agent_id").lower() == "agenthrljoint" and self.parameter.get("disease_as_action") == False:
            #print('##############')
            #if master_action_index==9:
            #    print(master_action_index)  通过action_type的筛选已经去除了master_action_index为9的情形
            self.acc_by_group[group_id][2] += 1
            if self.master_action_space[master_action_index] == group_id:
                #reward = 10
                self.acc_by_group[group_id][1] += 1
                if self.lower_reward_function(state=state, next_state=self.state_tracker.get_state()) > 0:
                    self.acc_by_group[group_id][0] += 1
            #else:
            #    reward = -1

            alpha = 88
            if lower_action in self.action_history:
                print('Penalized for lower action reapeatition  :')
                lower_reward = -alpha
            else:
                lower_reward1 = alpha * self.lower_reward_function(state=state, next_state=self.state_tracker.get_state())
                lower_reward = max(0, lower_reward1)
                reward = lower_reward

        if self.parameter.get('agent_id').lower()=='agenthrljoint2' and lower_action in self.lower_action_history:
            print('Penalized for lower action repeatition')
            lower_reward = self.parameter.get("reward_for_repeated_action")

        if self.parameter.get("agent_id") == "agenthrljoint2" and self.parameter.get("disease_as_action")==False and agent_action["action"]!="inform":
            #print(master_action_index)
            r, lower_reward = self.next_by_hrl_joint2(dialogue_status, lower_action,user_action, state, master_action_index, group_id, episode_over, reward)
        else:
            if lower_action in self.action_history:
                reward = self.parameter.get("reward_for_repeated_action")
                #print("************************")
                self.repeated_action_count += 1
                episode_over = True
            else:
                self.action_history.append(lower_action)



        if dialogue_status == dialogue_configuration.DIALOGUE_STATUS_INFORM_WRONG_DISEASE:
            self.inform_wrong_disease_count += 1

        # if dialogue_status == dialogue_configuration.DIALOGUE_STATUS_SUCCESS:
        #     print("success:", self.state_tracker.user.state)
        # elif dialogue_status == dialogue_configuration.DIALOGUE_STATUS_NOT_COME_YET:
        #     print("not come:", self.state_tracker.user.state)
        # else:
        #     print("failed:", self.state_tracker.user.state)
        # if len(self.state_tracker.user.state["rest_slots"].keys()) ==0:
        #     print(self.state_tracker.user.goal)
        #     print(dialogue_status,self.state_tracker.user.state)
        #print(reward)


        if save_record is True:
            if self.parameter.get('prioritized_replay'):
                current_action_value = self.state_tracker.agent.current_action_value
                target_action_value = self.state_tracker.agent.next_state_values_DDQN(state)
                TD_error = reward + self.parameter.get("gamma") * target_action_value - current_action_value
                self.record_prioritized_training_sample(
                    state=state,
                    agent_action=lower_action_index,
                    next_state=self.state_tracker.get_state(),
                    reward=reward,
                    episode_over=episode_over,
                    TD_error=TD_error,
                    lower_reward = lower_reward
                )
            else:
                if self.parameter.get("initial_symptom") is False or self.state_tracker.get_state()["turn"]==2:
                    self.record_training_sample(
                        state=state,
                        agent_action=lower_action_index,
                        next_state=self.state_tracker.get_state(),
                        reward=reward,
                        episode_over=episode_over,
                        lower_reward = lower_reward,
                        master_action_index = master_action_index
                        )

        # Output the dialogue.
        slots_proportion_list = []
        if episode_over == True:
            self.action_history = []
            self.lower_action_history = []
            current_slots = copy.deepcopy(state["current_slots"]["inform_slots"])
            num_of_true_slots = 0
            real_implicit_slots = len(self.state_tracker.user.goal['goal']['implicit_inform_slots'])
            for values in current_slots.values():
                if values == True:
                    num_of_true_slots += 1
            num_of_all_slots = len(current_slots)
            slots_proportion_list.append(num_of_true_slots - 1)  #表示current slot问到的隐性slot的个数
            slots_proportion_list.append(num_of_all_slots - 1)   #表示current slot中含有的所有隐性slot的个数，即request的次数
            slots_proportion_list.append(real_implicit_slots)    #表示当前的goal 当中所含有的true slot的个数
            try:
                last_master_action = self.master_action_history[-1]
            except:
                last_master_action = -1
            if self.save_dialogue == True :
                state = self.state_tracker.get_state()
                goal = self.state_tracker.user.get_goal()
                self.__output_dialogue(state=state, goal=goal, master_history=self.master_action_history)
            if last_master_action == group_id:
                self.group_id_match += 1
            self.master_action_history = []
            if self.parameter.get("train_mode") == False and self.parameter.get("agent_id").lower()=="agenthrlnew2":
                #self.test_by_group[group_id][2] += 1
                #if last_master_action == group_id:
                #    self.test_by_group[group_id][1] += 1
                #    if reward == self.parameter.get("reward_for_success"):
                #        self.test_by_group[group_id][0] += 1
                if action_type == "disease":
                    self.test_by_group[last_master_action][2] += 1
                    if last_master_action == group_id:
                        self.test_by_group[last_master_action][1] += 1
                        if reward == self.parameter.get("reward_for_success") -user_action['turn']:
                            self.test_by_group[last_master_action][0] += 1

        return reward, episode_over, dialogue_status, slots_proportion_list

    def next_by_hrl_joint2(self, dialogue_status, lower_action, user_action,state, master_action_index, group_id, episode_over, reward):
        '''
                    self.acc_by_group[group_id][2] += 1
                    if self.master_action_space[master_action_index] == group_id:
                        self.acc_by_group[group_id][1] += 1
                        if self.lower_reward_function(state=state, next_state=self.state_tracker.get_state()) > 0:
                            self.acc_by_group[group_id][0] += 1
                    '''
        alpha = self.parameter.get("weight_for_reward_shaping")
        gamma = self.parameter.get("gamma")
        if dialogue_status == dialogue_configuration.DIALOGUE_STATUS_REACH_MAX_TURN:
            self.repeated_action_count += 1

        # if action_type == "return":
        #    lower_reward = alpha/2 * self.worker_right_inform_num
        # if action_type == "symptom" and self.state_tracker.agent.subtask_terminal:  #reach max turn
        #    lower_reward = -alpha
        if self.parameter.get("train_mode") == False:
            if lower_action not in self.lower_action_history:
                # self.lower_reward_by_group[self.master_action_space[master_action_index]].append(lower_reward)
                self.symptom_by_group[self.master_action_space[master_action_index]][1] += 1
                if self.lower_reward_function(state=state, next_state=self.state_tracker.get_state()) > 0:
                    self.symptom_by_group[self.master_action_space[master_action_index]][0] += 1


        lower_reward = alpha * self.lower_reward_function(state=state, next_state=self.state_tracker.get_state())
        ##More reward More Panalty (160621 -2*)
        if self.p ==1 and lower_action not in self.lower_action_history:
            print('Rewarded for taking potential symptoms')
            lower_reward = lower_reward + 2*gamma

        if self.p ==-1 and list(user_action['inform_slots'].values())[0] !=True:
            lower_reward = lower_reward - 2*gamma
            print('Penalized for not taking potential symptoms')

        if lower_action in self.lower_action_history:  # repeated action
            print('Penalized for repeating lower actions')

            lower_reward = -alpha
            self.state_tracker.agent.subtask_terminal = True
            # episode_over = True
            # self.repeated_action_count += 1
            reward = self.parameter.get("reward_for_repeated_action")
        # elif self.state_tracker.agent.subtask_terminal is False or lower_reward>0:
        else:
            lower_reward = max(0, lower_reward)
            if lower_reward > 0:
                self.state_tracker.agent.subtask_terminal = True
                self.worker_right_inform_num += 1
                lower_reward = alpha
            self.lower_action_history.append(lower_action)




            # if self.parameter.get("train_mode")==False:
            #    self.lower_reward_by_group[self.master_action_space[master_action_index]].append(lower_reward)
        # else:
        #    lower_reward = -alpha
        # if self.lower_reward_function(state=state, next_state=self.state_tracker.get_state())>0:
        #   self.worker_right_inform_num += 1
        # if  dialogue_status == dialogue_configuration.DIALOGUE_STATUS_REACH_MAX_TURN:
        if self.parameter.get("train_mode") == False:
            self.lower_reward_by_group[self.master_action_space[master_action_index]].append(lower_reward)

        if episode_over == True:
            self.state_tracker.agent.subtask_terminal = True
            self.state_tracker.agent.subtask_turn = 0
            '''
            #reward = alpha/2 * self.worker_right_inform_num - 2
            if dialogue_status != dialogue_configuration.DIALOGUE_STATUS_REACH_MAX_TURN:
                if self.master_action_space[master_action_index] == group_id:
                    reward = 10
                else:
                    reward = -1
            self.worker_right_inform_num = 0
            #self.lower_action_history = []
            '''
        elif self.state_tracker.agent.subtask_terminal:
            # reward = alpha / 2 * self.worker_right_inform_num - 2
            '''
            if self.master_action_space[master_action_index] == group_id:
                reward = 10
            else:
                reward = -1
            '''
            self.worker_right_inform_num = 0
            if self.parameter.get("train_mode") == False:
                self.master_index_by_group.append([group_id, master_action_index])
            # self.lower_action_history = []
        return reward, lower_reward






    def initialize(self, dataset, goal_index=None):
        self.state_tracker.initialize()
        self.inform_wrong_disease_count = 0
        user_action = self.state_tracker.user.initialize(dataset=dataset, goal_index=goal_index)
        self.state_tracker.state_updater(user_action=user_action)
        self.state_tracker.agent.initialize()
        # self.group_id_match = 0
        # print("#"*30 + "\n" + "user goal:\n", json.dumps(self.state_tracker.user.goal))
        # state = self.state_tracker.get_state()
        # print("turn:%2d, initialized state:\n" % (state["turn"]), json.dumps(state))

    def record_training_sample(self, state, agent_action, reward, next_state, episode_over, **kwargs):
        if self.parameter.get("agent_id").lower() in ["agenthrljoint",'agenthrljoint2']:
            lower_reward = kwargs.get("lower_reward")
            master_action_index = kwargs.get("master_action_index")
            self.state_tracker.agent.record_training_sample(state, agent_action, reward, next_state, episode_over, lower_reward, master_action_index)
        else:
            self.state_tracker.agent.record_training_sample(state, agent_action, reward, next_state, episode_over)

    def record_prioritized_training_sample(self, state, agent_action, reward, next_state, episode_over, TD_error, **kwargs):
        if self.parameter.get("agent_id").lower() in ["agenthrljoint",'agenthrljoint2']:
            lower_reward = kwargs.get("lower_reward")
            self.state_tracker.agent.record_prioritized_training_sample(state, agent_action, reward, next_state,
                                                                        episode_over, TD_error, lower_reward)
        else:
            self.state_tracker.agent.record_prioritized_training_sample(state, agent_action, reward, next_state, episode_over, TD_error)

    def set_agent(self,agent):
        self.state_tracker.set_agent(agent=agent)

    def train(self):
        self.state_tracker.agent.train_dqn()
        self.state_tracker.agent.update_target_network()

    def __output_dialogue(self,state, goal, master_history):
        history = state["history"]
        file = open(file=self.dialogue_output_file,mode="a+",encoding="utf-8")
        file.write("User goal: " + str(goal)+"\n")
        for turn in history:
            #print(turn)
            try:
                speaker = turn["speaker"]
            except:
                speaker = 'agent'
            action = turn["action"]
            inform_slots = turn["inform_slots"]
            request_slots = turn["request_slots"]
            if speaker == "agent":
                try:
                    master_action = master_history.pop(0)
                    file.write(speaker + ": master+ " + str(master_action) +' +'+ action + "; inform_slots:"
                               + str(inform_slots) + "; request_slots:" + str(request_slots) + "\n")
                except:
                    file.write(speaker + ": master+ " + ' +' + action + "; inform_slots:" + str(inform_slots)
                               + "; request_slots:" + str(request_slots) + "\n")
            else:
                file.write(speaker + ": " + action + "; inform_slots:" + str(inform_slots) + "; request_slots:" + str(request_slots) + "\n")
        file.write("\n\n")
        assert len(master_history) == 0
        file.close()

    def lower_reward_function(self, state, next_state):
        """
        The reward for lower agent
        :param state:
        :param next_state:
        :return:
        """
        def delete_item_from_dict(item, value):
            new_item = {}
            for k, v in item.items():
                if v != value: new_item[k] = v
            return new_item

        # slot number in state.
        slot_dict = copy.deepcopy(state["current_slots"]["inform_slots"])
        slot_dict = delete_item_from_dict(slot_dict, dialogue_configuration.I_DO_NOT_KNOW)



        next_slot_dict = copy.deepcopy(next_state["current_slots"]["inform_slots"])
        next_slot_dict = delete_item_from_dict(next_slot_dict, dialogue_configuration.I_DO_NOT_KNOW)
        gamma = self.parameter.get("gamma")

        return  gamma*(len(next_slot_dict) - len(slot_dict))
        #return max(0, gamma * len(next_slot_dict) - len(slot_dict))







    def current_state_representation(self, state):
        """
        The state representation for the input of disease classifier.
        :param state: the last dialogue state before fed into disease classifier.
        :return: a vector that has equal length with slot set.
        """
        assert 'disease' not in self.slot_set.keys()
        state_rep = [0]*len(self.slot_set)
        current_slots = copy.deepcopy(state['current_slots'])
        if self.parameter.get('data_type') == 'simulated':
            for slot, value in current_slots['inform_slots'].items():
                if value == True:
                    state_rep[self.slot_set[slot]] = 1
                # elif value == "I don't know.":
                #    state_rep[self.slot_set[slot]] = -1
                #else:
                #    print(value)
                #    raise ValueError("the slot value of inform slot is not among True and I don't know")
            #print(state_rep)
        elif self.parameter.get('data_type') == 'real':
            for slot, value in current_slots['inform_slots'].items():
                if value == True:
                    state_rep[self.slot_set[slot]] = 1
                elif value == False:
                    state_rep[self.slot_set[slot]] = -1
        return state_rep






    def current_state_representation_both(self, state):
        assert 'disease' not in self.slot_set.keys()
        state_rep = np.zeros((len(self.slot_set.keys()), 3))
        current_slots = copy.deepcopy(state['current_slots'])
        #for slot, value in current_slots['inform_slots'].items():
        for slot in self.slot_set:
            if slot in current_slots['inform_slots']:
                if current_slots['inform_slots'][slot] == True:
                    state_rep[self.slot_set[slot],:] = [1,0,0]
                elif current_slots['inform_slots'][slot] == dialogue_configuration.I_DO_NOT_KNOW:
                    state_rep[self.slot_set[slot],:] = [0,1,0]
                else:
                    state_rep[self.slot_set[slot],:] = [0,0,1]
            else:
                state_rep[self.slot_set[slot],:] = [0,0,1]
                #raise ValueError("the slot value of inform slot is not among True and I don't know")
        state_rep1 = state_rep.reshape(1, len(self.slot_set.keys()) * 3)[0]
        return state_rep1

    def train_ml_classifier(self):
        goal_set = pickle.load(open(self.parameter.get("goal_set"),'rb'))
        disease_y = []
        total_set = random.sample(goal_set['train'], 5000)

        slots_exp = np.zeros((len(total_set), len(self.slot_set)))
        for i, dialogue in enumerate(total_set):
            tag = dialogue['disease_tag']
            # tag_group=disease_symptom1[tag]['symptom']
            disease_y.append(tag)
            goal = dialogue['goal']
            explicit = goal['explicit_inform_slots']
            for exp_slot, value in explicit.items():
                try:
                    slot_id = self.slot_set[exp_slot]
                    if value == True:
                        slots_exp[i, slot_id] = '1'
                except:
                    pass


        self.modelS.fit(slots_exp, disease_y)

    def build_deep_learning_classifier(self):
        self.model = dl_classifier(input_size=len(self.slot_set), hidden_size=256,
                                   output_size=len(self.disease_symptom),
                                   parameter=self.parameter)
        if self.parameter.get("train_mode") == False:
            temp_path = self.parameter.get("saved_model")
            path_list = temp_path.split('/')
            path_list.insert(-1, 'classifier')
            saved_model = '/'.join(path_list)
            print('Inside build deep learning classifier:',saved_model)
            self.model.restore_model(saved_model)
            print('loaded')
            self.model.eval_mode()



    def build_machine_learning_classifier(self):

        # self.modelG = dl_classifier(input_size=len(self.slot_set), hidden_size=256,
        #                            output_size = len(self.grp),
        #                            parameter=self.parameter)
        self.modelS = svm.SVC(kernel='linear', C=1)

        if self.parameter.get("train_mode") == False:
            temp_path = self.parameter.get("saved_model")
            path_list = temp_path.split('/')
            path_list.insert(-1, 'classifierM')
            saved_model = '/'.join(path_list)
            self.modelS.restore_model(saved_model)
            self.modelS.eval_mode()

    def train_deep_learning_classifier(self, epochs):
        #self.model.train_dl_classifier(epochs=5000)
        #print("############   the deep learning model is training over  ###########")
        # for iter in range(epochs):
        #     batch = random.sample(self.disease_replay, min(self.parameter.get("batch_size"),len(self.disease_replay)))
        #     loss = self.model.train(batch=batch)

        test_batch = random.sample(self.disease_replay, min(1000,len(self.disease_replay)))
        test_acc = self.model.test(test_batch=test_batch)
        #print('disease_replay:{},loss:{:.4f}, test_acc of Diease Classifier:{:.4f}'.format(len(self.disease_replay), loss["loss"], test_acc))

        SS = {'Accuracy':test_acc}
        with open("Dtest.json", "a") as outfile:
            json.dump(SS, outfile)
        return test_acc


        #self.model.test_dl_classifier()

    def save_dl_model(self, model_performance, episodes_index, checkpoint_path=None):
        # Saving master agent
        temp_checkpoint_path = os.path.join(checkpoint_path, 'classifier/')
        self.model.save_model(model_performance=model_performance, episodes_index=episodes_index, checkpoint_path=temp_checkpoint_path)


    def save_ml_model(self, model_performance, episodes_index, checkpoint_path=None):
        # Saving master agent
        temp_checkpoint_path = os.path.join(checkpoint_path, 'classifierM/')
        temp_checkpoint_pathG = os.path.join(checkpoint_path, 'classifierG/')

        #self.modelS.save_model(model_performance=model_performance, episodes_index=episodes_index, checkpoint_path=temp_checkpoint_path)


    def exp_transform(self, x):
        exp_sum = 0
        for i in x:
            exp_sum += np.exp(i)
        return [np.exp(i)/exp_sum for i in x]

    def Dtop5Sym(self, top5):
        probable_sym = [0]*len(self.slot_set)
        all_symp = list(self.slot_set.values())
        for i in range(0,len(all_symp)):
            if i in top5:
                probable_sym[i] = 1
        return probable_sym
