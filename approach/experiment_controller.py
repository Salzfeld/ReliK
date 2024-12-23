import argparse
import os
import torch
import pandas as pd
import networkx as nx
import csv
import timeit
import itertools

import embedding as emb
import datahandler as dh
import classifier as cla

from pykeen.models import TransE
from pykeen.models import ERModel
from pykeen.pipeline import pipeline

from pykeen.triples import TriplesFactory
from pykeen.triples import CoreTriplesFactory
from pykeen.datasets import Dataset
import gc
import random
import numpy as np

import os

def makeTCPart(LP_triples_pos, LP_triples_neg, entity2embedding, relation2embedding, subgraphs, emb_train_triples, classifier):
    '''
    function to access and run triple classification with trained classifiers
    '''
    X_train, X_test, y_train, y_test = cla.prepareTrainTestData(LP_triples_pos, LP_triples_neg, emb_train_triples)
    clf = cla.trainClassifier(X_train, y_train, entity2embedding, relation2embedding, type=classifier)
    LP_test_score = cla.testClassifierSubgraphs(clf, X_test, y_test, entity2embedding, relation2embedding, subgraphs)

    return LP_test_score

def naiveTripleCLassification(LP_triples_pos, LP_triples_neg, entity_to_id_map, relation_to_id_map, subgraphs, emb_train_triples, model):
    '''
    function to access and run triple classification with a naive threshold classifiers
    '''
    LP_test_score = []
    X_train_pos, X_test_pos, y_train_pos, y_test_pos, X_train_neg, X_test_neg, y_train_neg, y_test_neg = cla.prepareTrainTestDataSplit(LP_triples_pos, LP_triples_neg, emb_train_triples, entity_to_id_map, relation_to_id_map)
    first = True
    for i in range(len(X_train_pos)):
        if first:
            first = False
            rslt_torch_pos = X_train_pos[i][0]
            rslt_torch_pos = rslt_torch_pos.resize_(1,3)
            
        else:
            rslt_torch_pos = torch.cat((rslt_torch_pos,  X_train_pos[i][0].resize_(1,3)))
    first = True
    for i in range(len(X_train_neg)):
        if first:
            first = False
            rslt_torch_neg = X_train_neg[i][0]
            rslt_torch_neg = rslt_torch_neg.resize_(1,3)
            
        else:
            rslt_torch_neg = torch.cat((rslt_torch_neg,  X_train_neg[i][0].resize_(1,3)))
    comp_score_pos = model.score_hrt(rslt_torch_pos)
    comp_score_neg = model.score_hrt(rslt_torch_neg)

    first = True
    for i in range(torch.sum(comp_score_neg > torch.min(comp_score_pos)).cpu().detach().numpy()):
        k = torch.topk(comp_score_neg, i+1, dim=-2)
        pos_low = torch.sum(comp_score_pos < k.values[i]).cpu().detach().numpy()
        neg_hig = torch.sum(comp_score_neg > k.values[i]).cpu().detach().numpy()
        if first:
            min_false = pos_low + neg_hig
            first = False
        if min_false > neg_hig + pos_low:
            thresh = k.values[i]
            min_false = neg_hig + pos_low
    first = True
    for subgraph in subgraphs:
        for tp in X_test_pos:
            if ((emb_train_triples.entity_id_to_label[tp[0][0].item()] in subgraph) or (emb_train_triples.entity_id_to_label[tp[0][2].item()] in subgraph)):
                if first:
                    first = False
                    rslt_torch_pos = tp[0]
                    rslt_torch_pos = rslt_torch_pos.resize_(1,3)
                else:
                    rslt_torch_pos = torch.cat((rslt_torch_pos, tp[0].resize_(1,3)))
        first = True
        for tp in X_test_neg:
            if ((emb_train_triples.entity_id_to_label[tp[0][0].item()] in subgraph) or (emb_train_triples.entity_id_to_label[tp[0][2].item()] in subgraph)):
                if first:
                    first = False
                    rslt_torch_neg = tp[0]
                    rslt_torch_neg = rslt_torch_neg.resize_(1,3)
                else:
                    rslt_torch_neg = torch.cat((rslt_torch_neg, tp[0].resize_(1,3)))
    
        
        comp_score_pos = model.score_hrt(rslt_torch_pos)
        comp_score_neg = model.score_hrt(rslt_torch_neg)
            
        pos_low = torch.sum(comp_score_pos < thresh).cpu().detach().numpy()
        neg_hig = torch.sum(comp_score_neg > thresh).cpu().detach().numpy()

        LP_test_score.append((rslt_torch_pos.shape[0]+rslt_torch_neg.shape[0]-(pos_low+neg_hig))/(rslt_torch_pos.shape[0]+rslt_torch_neg.shape[0]))
    return LP_test_score


def grabAllKFold(datasetname: str, n_split: int, embeddingname: str = 'TransE'):
    '''
    loading all parts of the KFold in its needed way for the experiments
    '''
    all_triples, all_triples_set, entity_to_id_map, relation_to_id_map, test_triples, validation_triples = emb.getDataFromPykeen(datasetname=datasetname)
    full_dataset = torch.cat((all_triples, test_triples.mapped_triples, validation_triples.mapped_triples))

    isExist = os.path.exists(f"approach/KFold/{datasetname}_{n_split}_fold")
    full_dataset = torch.cat((all_triples, test_triples.mapped_triples, validation_triples.mapped_triples))
    if not isExist:
        dh.generateKFoldSplit(full_dataset, datasetname, random_seed=None, n_split=nmb_KFold)
    full_dataset = torch.cat((all_triples, test_triples.mapped_triples, validation_triples.mapped_triples))

    full_graph = TriplesFactory(full_dataset,entity_to_id=entity_to_id_map,relation_to_id=relation_to_id_map)

    # Need to store the ids of all positive triples on GPU
    global all_triple_id_torch
    all_triple_id_torch = encode_triples_to_id(full_dataset, full_graph.num_entities, full_graph.num_relations)
    all_triple_id_torch = all_triple_id_torch.to('cuda')
    #print("check: ", full_dataset.device, all_triple_id_torch.device)

    emb_train_triples = []
    emb_test_triples = []
    LP_triples_pos = []
    for i in range(n_split):
        emb_triples_id, LP_triples_id = dh.loadKFoldSplit(i, datasetname,n_split=nmb_KFold)
        emb_triples = full_dataset[emb_triples_id]
        LP_triples = full_dataset[LP_triples_id]
        if embeddingname != 'CompGCN':
            emb_train_triples.append(TriplesFactory(emb_triples,entity_to_id=entity_to_id_map,relation_to_id=relation_to_id_map))
            emb_test_triples.append(TriplesFactory(LP_triples,entity_to_id=entity_to_id_map,relation_to_id=relation_to_id_map))
        else:
            emb_train_triples.append(TriplesFactory(emb_triples,entity_to_id=entity_to_id_map,relation_to_id=relation_to_id_map,create_inverse_triples=True))
            emb_test_triples.append(TriplesFactory(LP_triples,entity_to_id=entity_to_id_map,relation_to_id=relation_to_id_map,create_inverse_triples=True))

        LP_triples_pos.append(LP_triples.tolist())

    return all_triples, all_triples_set, entity_to_id_map, relation_to_id_map, emb_train_triples, emb_test_triples, LP_triples_pos, full_graph

def getOrTrainModels(embedding: str, dataset_name: str, n_split: int, emb_train_triples, emb_test_triples, device):
    '''
    get the already trained model if exists, otherwise train one accordingly
    '''
    models = []
    for i in range(n_split):
        isFile = os.path.isfile(f"approach/trainedEmbeddings/{dataset_name}_{embedding}_{n_split}_fold/{dataset_name}_{i}th/trained_model.pkl")
        if not isFile:
            save = f"{dataset_name}_{embedding}_{n_split}_fold/{dataset_name}_{i}th"
            emb_model, emb_triples_used = emb.trainEmbedding(emb_train_triples[i], emb_test_triples[i], random_seed=42, saveModel=True, savename = save, embedd = embedding, dimension = 50, epoch_nmb = 50)
            models.append(emb_model)
        else:
            save = f"{dataset_name}_{embedding}_{n_split}_fold/{dataset_name}_{i}th"
            models.append(emb.loadModel(save,device=device))

    return models

def KFoldNegGen(datasetname: str, n_split: int, all_triples_set, LP_triples_pos, emb_train):
    '''
    create negative triples that are needed for certain experiments, if some got created already, use those
    '''
    isFile = os.path.isfile(f"approach/KFold/{datasetname}_{n_split}_fold/0th_neg.csv")
    LP_triples_neg = []
    if not isFile:
        for i in range(n_split):
            neg_triples, throw = dh.createNegTripleHT(all_triples_set, LP_triples_pos[i], emb_train[i])
            dh.storeTriples(f"approach/KFold/{datasetname}_{n_split}_fold/{i}th_neg", neg_triples)
            LP_triples_neg.append(neg_triples)
    else:
        for i in range(n_split):
            neg_triples = dh.loadTriples(f"approach/KFold/{datasetname}_{n_split}_fold/{i}th_neg")
            LP_triples_neg.append(neg_triples)
    return LP_triples_neg
### Focus on this function

#import multiprocessing as mp
import torch.multiprocessing as mp

parallel_uv = False

def process_edges_partition(edge_partition, heur, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities, num_relations, sample, datasetname, results, device='cuda', perm_entities=None, perm_relations=None, all_triple_id_torch=None):
    #count = 0
    sib_sum = 0
    sib_sum_h = 0
    sib_sum_t = 0
    
    # Process each edge in the partition
    if heur.__name__ == 'binomial_cuda':
        for u, v in edge_partition:
            w, w1, w2 = heur(u, v, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities, num_relations, sample, datasetname, device, perm_entities, perm_relations, all_triple_id_torch)
            #count += 1
            sib_sum += w
            sib_sum_h += w1
            sib_sum_t += w2
    else:
        for u, v in edge_partition:
            w, w1, w2 = heur(u, v, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities, num_relations, sample, datasetname)
            #count += 1
            sib_sum += w
            sib_sum_h += w1
            sib_sum_t += w2
    print(f"Success: {os.getpid()}")
    results.append((sib_sum, sib_sum_h, sib_sum_t))

getkHopneighbors_time = 0.0
sample_time = 0.0
entity_relation_loop_time = 0.0
model_loop_time = 0.0
sample_first_while_time = 0.0
sample_second_while_time = 0.0

def DoGlobalReliKScore(embedding, datasetname, n_split, size_subgraph, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, sample, heur):
    '''
    compute the ReliK score on all subgraphs according to chosen heuristic
    '''
    global getkHopneighbors_time, sample_time, entity_relation_loop_time, model_loop_time, sample_first_while_time, sample_second_while_time
    getkHopneighbors_time = 0.0
    sample_time = 0.0
    entity_relation_loop_time = 0.0
    model_loop_time = 0.0
    sample_first_while_time = 0.0
    sample_second_while_time = 0.0
    df = pd.DataFrame(full_graph.triples, columns=['subject', 'predicate', 'object'])
    M = nx.MultiDiGraph()

    subgraphs = dh.loadSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", size_subgraphs)
    if len(subgraphs) < n_subgraphs:
        subgraphs_new = dh.createSubGraphs(all_triples, entity_to_id_map, relation_to_id_map, size_of_graphs=size_subgraphs, number_of_graphs=(n_subgraphs-len(subgraphs)))
        dh.storeSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", subgraphs_new)
        subgraphs = subgraphs + subgraphs_new
    if len(subgraphs) > n_subgraphs:
        subgraphs = subgraphs[:n_subgraphs]

    for t in df.values:
        M.add_edge(t[0], t[2], label = t[1])


    model_ReliK_score = []
    model_ReliK_score_h = []
    model_ReliK_score_t = []
    tracker = 0

    num_entities_par = full_graph.num_entities
    num_relations_par = full_graph.num_relations
    #print(full_graph.num_triples, full_graph.num_entities)
    ### HERE!!!
    if parallel_uv is False:
        if heur.__name__ == 'binomial_cuda':
            perm_entities, perm_relations = pre_randperm(full_graph.num_entities, full_graph.num_relations)
            for subgraph in subgraphs:
                count = 0
                sib_sum = 0
                sib_sum_h = 0
                sib_sum_t = 0
                start_uv = timeit.default_timer()
                for u,v in nx.DiGraph(M).subgraph(subgraph).edges():
                    w, w1, w2 = heur(u, v, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities_par, num_relations_par, sample, datasetname, 'cuda', perm_entities, perm_relations, all_triple_id_torch)
                    count += 1
                    sib_sum += w
                    sib_sum_h += w1
                    sib_sum_t += w2
                end_uv = timeit.default_timer()
                print(f'have done subgraph: {id(subgraph)} in {end_uv - start_uv}, with {count} edges')

                sib_sum = sib_sum/count
                sib_sum_h = sib_sum_h/count
                sib_sum_t = sib_sum_t/count
                model_ReliK_score.append(sib_sum)
                model_ReliK_score_h.append(sib_sum_h)
                model_ReliK_score_t.append(sib_sum_t)
                tracker += 1
                if tracker % 10 == 0:
                    print(f'have done {tracker} of {len(subgraphs)} in {embedding}')
        #start_time_enum_subgraph = timeit.default_timer()
        else:
            for subgraph in subgraphs:
                count = 0
                sib_sum = 0
                sib_sum_h = 0
                sib_sum_t = 0
                start_uv = timeit.default_timer()
                for u,v in nx.DiGraph(M).subgraph(subgraph).edges():
                    w, w1, w2 = heur(u, v, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities_par, num_relations_par, sample, datasetname)
                    count += 1
                    sib_sum += w
                    sib_sum_h += w1
                    sib_sum_t += w2
                end_uv = timeit.default_timer()
                print(f'have done subgraph: {id(subgraph)} in {end_uv - start_uv}, with {count} edges')

                sib_sum = sib_sum/count
                sib_sum_h = sib_sum_h/count
                sib_sum_t = sib_sum_t/count
                model_ReliK_score.append(sib_sum)
                model_ReliK_score_h.append(sib_sum_h)
                model_ReliK_score_t.append(sib_sum_t)
                tracker += 1
                if tracker % 10 == 0:
                    print(f'have done {tracker} of {len(subgraphs)} in {embedding}')
        print(f"Total time for dh.getkHopneighbors: {getkHopneighbors_time} seconds")
        print(f"Total time for sampling: {sample_time} seconds")
        print(f"Total time for model loop: {model_loop_time} seconds")
        print(f"Total time for sample first while loop: {sample_first_while_time} seconds")
        print(f"Total time for sample second while loop: {sample_second_while_time} seconds")
        #end_time_enum_subgraph = timeit.default_timer()
        #print(f'Enum subgraph time: {end_time_enum_subgraph - start_time_enum_subgraph}')
    else: # parallel_uv = True
        # Try to use multi processors
        mp.set_start_method('spawn', force=True)
        perm_entities, perm_relations = pre_randperm(full_graph.num_entities, full_graph.num_relations)
        # Use Manager to create shared lists
        """
        manager = mp.Manager()
        perm_entities = manager.list(perm_entities)
        perm_relations = manager.list(perm_relations)
        """

        for subgraph in subgraphs:
            count = 0
            sib_sum = 0
            sib_sum_h = 0
            sib_sum_t = 0
            start_uv = timeit.default_timer()
            
            edges = list(nx.DiGraph(M).subgraph(subgraph).edges())
            count = len(edges)
            num_processors = 10

            chunk_size = len(edges) // num_processors

            edge_chunks = [edges[i * chunk_size:(i + 1) * chunk_size] if i < num_processors - 1 else edges[i * chunk_size:] for i in range(num_processors)]

            manager = mp.Manager()
            results = manager.list()

            processes = []
            if heur.__name__ == 'binomial_cuda':
                for i,edge_chunk in enumerate(edge_chunks):
                    p = mp.Process(target=process_edges_partition, args=(edge_chunk, heur, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities_par, num_relations_par, sample, datasetname, results, 'cuda', perm_entities, perm_relations, all_triple_id_torch))
                    p.start()
                    processes.append(p)
            else:
                for i,edge_chunk in enumerate(edge_chunks):
                    p = mp.Process(target=process_edges_partition, args=(edge_chunk, heur, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, num_entities_par, num_relations_par, sample, datasetname, results))
                    p.start()
                    processes.append(p)
            for p in processes:
                p.join()
            
            sib_sum = sum([r[0] for r in results])
            sib_sum_h = sum([r[1] for r in results])
            sib_sum_t = sum([r[2] for r in results])
            #print(sib_sum, sib_sum_h, sib_sum_t)
            
            end_uv = timeit.default_timer()
            print(f'have done subgraph: {id(subgraph)} in {end_uv - start_uv}')

            sib_sum = sib_sum/count
            sib_sum_h = sib_sum_h/count
            sib_sum_t = sib_sum_t/count
            model_ReliK_score.append(sib_sum)
            model_ReliK_score_h.append(sib_sum_h)
            model_ReliK_score_t.append(sib_sum_t)
            tracker += 1
            if tracker % 10 == 0:
                print(f'have done {tracker} of {len(subgraphs)} in {embedding}')
        print(f"Total time for dh.getkHopneighbors: {getkHopneighbors_time} seconds")
        print(f"Total time for sampling: {sample_time} seconds")
        print(f"Total time for model loop: {model_loop_time} seconds")
        print(f"Total time for sample first while loop: {sample_first_while_time} seconds")
        print(f"Total time for sample second while loop: {sample_second_while_time} seconds")

    path = f"approach/scoreData/{datasetname}_{n_split}/{embedding}/ReliK_score_subgraphs_{size_subgraph}.csv"
    if parallel_uv is True:
        path = f"approach/scoreData/{datasetname}_{n_split}/{embedding}/ReliK_score_subgraphs_{size_subgraph}_parallel.csv"
    c = open(f'{path}', "w")
    writer = csv.writer(c)
    data = ['subgraph','ReliK','sib h','sib t']
    writer.writerow(data)
    for j in range(len(model_ReliK_score)):
        data = [j, model_ReliK_score[j], model_ReliK_score_h[j], model_ReliK_score_t[j]]
        writer.writerow(data)
    c.close()

def randomsample(embedding, datasetname, n_split, size_subgraph, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, sample, heur):
    '''
    print ReliK scores for a random sample of the data
    '''
    df = pd.DataFrame(full_graph.triples, columns=['subject', 'predicate', 'object'])
    M = nx.MultiDiGraph()

    for t in df.values:
        M.add_node(t[0])
        M.add_node(t[2])
        M.add_edge(t[0], t[2], label = t[1])
    rand = random.choice(list(nx.DiGraph(M).edges()))
    print(set(M.neighbors(rand[0])))
    w = binomial(rand[0], rand[1], M, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, sample, datasetname)
    rr = RR(rand[0], rand[1], M, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, sample, datasetname)
    print(rand)
    print(w)
    print(rr)
    print(len(set(list(M.neighbors(rand[0]))+list(M.neighbors(rand[1])))))
    M.remove_nodes_from([n for n in M if n not in set(list(M.neighbors(rand[0]))+list(M.neighbors(rand[1])))])
    print(len(M.nodes))
    
def classifierExp(embedding, datasetname, size_subgraph, LP_triples_pos,  LP_triples_neg, entity2embedding, relation2embedding, emb_train, n_split, models, entity_to_id_map, relation_to_id_map, classifier):
    '''
    running the classification experiment parts on the subgraphs
    '''
    subgraphs = list[set[str]]()
    
    with open(f"approach/KFold/{datasetname}_{n_split}_fold/subgraphs_{size_subgraph}.csv", "r") as f:
        rows = csv.reader(f, delimiter=',')
        for row in rows:
            subgraph = set[str]()
            for ele in row:
                subgraph.add(ele)
            subgraphs.append(subgraph)
    
    score_cla = []
    for i in range(n_split):
        #LP_test_score = makeTCPart(LP_triples_pos[i],  LP_triples_neg[i], entity2embedding, relation2embedding, subgraphs, emb_train[i], classifier)
        #score_cla.append(LP_test_score)
        LP_test_score = naiveTripleCLassification(LP_triples_pos[i],  LP_triples_neg[i], entity_to_id_map, relation_to_id_map, subgraphs, emb_train[i], models[i])
        score_cla.append(LP_test_score)

    fin_score_cla = []
    for i in range(len(score_cla[0])):
        sumcla = 0
        measured = 0
        for j in range(n_split):
            if score_cla[j][i] >= 0:
                sumcla += score_cla[j][i]
                measured += 1
        if measured == 0:
            fin_score_cla.append(-100)
        else:
            fin_score_cla.append(sumcla/n_split)

    path = f"approach/scoreData/{datasetname}_{n_split}/{embedding}/classifier_score_subgraphs_{size_subgraph}_{classifier}.csv"
    c = open(f'{path}', "w")
    writer = csv.writer(c)
    data = ['subgraph','classifier']
    writer.writerow(data)
    for j in range(len(fin_score_cla)):
        data = [j, fin_score_cla[j]]
        writer.writerow(data)
    c.close()

def prediction(embedding, datasetname, size_subgraph, emb_train, all_triples_set, n_split):
    '''
    doing the tail and relation prediction experiments on the subgraphs
    '''
    subgraphs = dh.loadSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", size_subgraphs)
    if len(subgraphs) < n_subgraphs:
        subgraphs_new = dh.createSubGraphs(all_triples, entity_to_id_map, relation_to_id_map, size_of_graphs=size_subgraphs, number_of_graphs=(n_subgraphs-len(subgraphs)))
        dh.storeSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", subgraphs_new)
        subgraphs = subgraphs + subgraphs_new
    if len(subgraphs) > n_subgraphs:
        subgraphs = subgraphs[:n_subgraphs]

    fin_score_tail_at1 = []
    fin_score_tail_at5 = []
    fin_score_tail_at10 = []
    fin_score_tail_atMRR = []

    fin_score_relation_at1 = []
    fin_score_relation_at5 = []
    fin_score_relation_at10 = []
    fin_score_relation_atMRR = []
    for subgraph in subgraphs:
        model_relation_sum_at_1 = []
        model_relation_sum_at_5 = []
        model_relation_sum_at_10 = []
        model_relation_sum_for_MRR = []

        model_tail_sum_at_1 = []
        model_tail_sum_at_5 = []
        model_tail_sum_at_10 = []
        model_tail_sum_for_MRR = []
        for i in range(n_split):
            relation_sum_at_1 = 0
            relation_sum_at_5 = 0
            relation_sum_at_10 = 0
            relation_sum_for_MRR = 0

            tail_sum_at_1 = 0
            tail_sum_at_5 = 0
            tail_sum_at_10 = 0
            tail_sum_for_MRR = 0
            counter_of_test_tp = 0
            for tp in LP_triples_pos[i]:
                if (emb_train[i].entity_id_to_label[tp[0]] in subgraph) and (emb_train[0].entity_id_to_label[tp[2]] in subgraph):
                    counter_of_test_tp += 1
                    ten = torch.tensor([[tp[0],tp[1],tp[2]]])
                    comp_score = models[i].score_hrt(ten)

                    list_tail = torch.tensor([i for i in range(emb_train[0].num_entities) if (tp[0],tp[1], i) not in all_triples_set ])
                    list_relation = torch.tensor([i for i in range(emb_train[0].num_relations) if (tp[0],i, tp[2]) not in all_triples_set ])

                    tail_rank = torch.sum(models[i].score_t(ten[0][:2].resize_(1,2), tails=list_tail) > comp_score).cpu().detach().numpy() + 1
                    relation_rank = torch.sum(models[i].score_r(torch.cat([ten[0][:1], ten[0][1+1:]]).resize_(1,2), relations=list_relation) > comp_score).cpu().detach().numpy() + 1
                    if relation_rank <= 1:
                        relation_sum_at_1 += 1
                        relation_sum_at_5 += 1
                        relation_sum_at_10 += 1
                    elif relation_rank <= 5:
                        relation_sum_at_5 += 1
                        relation_sum_at_10 += 1
                    elif relation_rank <= 10:
                        relation_sum_at_10 += 1
                    relation_sum_for_MRR += 1/relation_rank

                    if tail_rank <= 1:
                        tail_sum_at_1 += 1
                        tail_sum_at_5 += 1
                        tail_sum_at_10 += 1
                    elif tail_rank <= 5:
                        tail_sum_at_5 += 1
                        tail_sum_at_10 += 1
                    elif tail_rank <= 10:
                        tail_sum_at_10 += 1
                    tail_sum_for_MRR += 1/tail_rank
            if counter_of_test_tp > 0:
                model_tail_sum_at_1.append(tail_sum_at_1/counter_of_test_tp)
                model_tail_sum_at_5.append(tail_sum_at_5/counter_of_test_tp)
                model_tail_sum_at_10.append(tail_sum_at_10/counter_of_test_tp)
                model_tail_sum_for_MRR.append(tail_sum_for_MRR/counter_of_test_tp)

                model_relation_sum_at_1.append(relation_sum_at_1/counter_of_test_tp)
                model_relation_sum_at_5.append(relation_sum_at_5/counter_of_test_tp)
                model_relation_sum_at_10.append(relation_sum_at_10/counter_of_test_tp)
                model_relation_sum_for_MRR.append(relation_sum_for_MRR/counter_of_test_tp)
        if len(model_relation_sum_at_1) > 0:
            fin_score_tail_at1.append(np.mean(model_tail_sum_at_1))
            fin_score_tail_at5.append(np.mean(model_tail_sum_at_5))
            fin_score_tail_at10.append(np.mean(model_tail_sum_at_10))
            fin_score_tail_atMRR.append(np.mean(model_tail_sum_for_MRR))

            fin_score_relation_at1.append(np.mean(model_relation_sum_at_1))
            fin_score_relation_at5.append(np.mean(model_relation_sum_at_5))
            fin_score_relation_at10.append(np.mean(model_relation_sum_at_10))
            fin_score_relation_atMRR.append(np.mean(model_relation_sum_for_MRR))
        else:
            fin_score_tail_at1.append(-100)
            fin_score_tail_at5.append(-100)
            fin_score_tail_at10.append(-100)
            fin_score_tail_atMRR.append(-100)

            fin_score_relation_at1.append(-100)
            fin_score_relation_at5.append(-100)
            fin_score_relation_at10.append(-100)
            fin_score_relation_atMRR.append(-100)

    path = f"approach/scoreData/{datasetname}_{n_split}/{embedding}/prediction_score_subgraphs_{size_subgraph}.csv"
    c = open(f'{path}', "w")
    writer = csv.writer(c)
    data = ['subgraph','Tail Hit @ 1','Tail Hit @ 5','Tail Hit @ 10','Tail MRR','Relation Hit @ 1','Relation Hit @ 5','Relation Hit @ 10','Relation MRR']
    writer.writerow(data)
    for j in range(len(fin_score_tail_at1)):
        data = [j, fin_score_tail_at1[j], fin_score_tail_at5[j], fin_score_tail_at10[j], fin_score_tail_atMRR[j], fin_score_relation_at1[j], fin_score_relation_at5[j], fin_score_relation_at10[j], fin_score_relation_atMRR[j]]
        writer.writerow(data)
    c.close()

def prediction_head(embedding, datasetname, size_subgraph, emb_train, all_triples_set, n_split):
    '''
    prediction experiments on for head prediction
    '''
    subgraphs = dh.loadSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", size_subgraphs)
    if len(subgraphs) < n_subgraphs:
        subgraphs_new = dh.createSubGraphs(all_triples, entity_to_id_map, relation_to_id_map, size_of_graphs=size_subgraphs, number_of_graphs=(n_subgraphs-len(subgraphs)))
        dh.storeSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", subgraphs_new)
        subgraphs = subgraphs + subgraphs_new
    if len(subgraphs) > n_subgraphs:
        subgraphs = subgraphs[:n_subgraphs]

    fin_score_head_at1 = []
    fin_score_head_at5 = []
    fin_score_head_at10 = []
    fin_score_head_atMRR = []

    for subgraph in subgraphs:
        model_head_sum_at_1 = []
        model_head_sum_at_5 = []
        model_head_sum_at_10 = []
        model_head_sum_for_MRR = []
        for i in range(n_split):
            head_sum_at_1 = 0
            head_sum_at_5 = 0
            head_sum_at_10 = 0
            head_sum_for_MRR = 0
            counter_of_test_tp = 0
            for tp in LP_triples_pos[i]:
                if (emb_train[i].entity_id_to_label[tp[0]] in subgraph) and (emb_train[0].entity_id_to_label[tp[2]] in subgraph):
                    counter_of_test_tp += 1
                    ten = torch.tensor([[tp[0],tp[1],tp[2]]])
                    comp_score = models[i].score_hrt(ten)

                    list_head = torch.tensor([i for i in range(emb_train[0].num_entities) if (i,tp[1], tp[2]) not in all_triples_set ])
                    head_rank = torch.sum(models[i].score_h(ten[0][1:].resize_(1,2), heads=list_head) > comp_score).cpu().detach().numpy() + 1

                    if head_rank <= 1:
                        head_sum_at_1 += 1
                        head_sum_at_5 += 1
                        head_sum_at_10 += 1
                    elif head_rank <= 5:
                        head_sum_at_5 += 1
                        head_sum_at_10 += 1
                    elif head_rank <= 10:
                        head_sum_at_10 += 1
                    head_sum_for_MRR += 1/head_rank
            if counter_of_test_tp > 0:
                model_head_sum_at_1.append(head_sum_at_1/counter_of_test_tp)
                model_head_sum_at_5.append(head_sum_at_5/counter_of_test_tp)
                model_head_sum_at_10.append(head_sum_at_10/counter_of_test_tp)
                model_head_sum_for_MRR.append(head_sum_for_MRR/counter_of_test_tp)
        if len(model_head_sum_at_1) > 0:
            fin_score_head_at1.append(np.mean(model_head_sum_at_1))
            fin_score_head_at5.append(np.mean(model_head_sum_at_5))
            fin_score_head_at10.append(np.mean(model_head_sum_at_10))
            fin_score_head_atMRR.append(np.mean(model_head_sum_for_MRR))
        else:
            fin_score_head_at1.append(-100)
            fin_score_head_at5.append(-100)
            fin_score_head_at10.append(-100)
            fin_score_head_atMRR.append(-100)

    path = f"approach/scoreData/{datasetname}_{n_split}/{embedding}/prediction_head_score_subgraphs_{size_subgraph}.csv"
    c = open(f'{path}', "w")
    writer = csv.writer(c)
    data = ['subgraph','head Hit @ 1','head Hit @ 5','head Hit @ 10','head MRR']
    writer.writerow(data)
    for j in range(len(fin_score_head_at1)):
        data = [j, fin_score_head_at1[j], fin_score_head_at5[j], fin_score_head_at10[j], fin_score_head_atMRR[j]]
        writer.writerow(data)
    c.close()

def storeTriplesYago(path, triples):
    '''
    dhelper to store yago2 dataset in expected format
    '''
    with open(f"{path}.csv", "w") as f:
        wr = csv.writer(f)
        wr.writerows(triples)

def Yago2():
    '''
    loading and then preparing the yago2 dataset for our programmic approach
    '''
    gc.collect()

    torch.cuda.empty_cache()
    data=pd.read_csv('approach/yago2core_facts.clean.notypes_3.tsv',sep='\t',names=['subject', 'predicate', 'object'])

    entity_to_id_map = {v: k for v, k in enumerate(pd.factorize(pd.concat([data['subject'],data['object']]))[1])}
    entity_to_id_map2 = {k: v for v, k in enumerate(pd.factorize(pd.concat([data['subject'],data['object']]))[1])}
    relation_to_id_map = {v: k for v, k in enumerate(pd.factorize(data['predicate'])[1])}
    relation_to_id_map2 = {k: v for v, k in enumerate(pd.factorize(data['predicate'])[1])}
    #print(len(entity_to_id_map))
    #print(data)
    data['subject'] = data['subject'].map(entity_to_id_map2)
    data['object'] = data['object'].map(entity_to_id_map2)  
    data['predicate'] = data['predicate'].map(relation_to_id_map2)  
    #data.replace({'subject': entity_to_id_map})
    #print(data)
    ten = torch.tensor(data.values)

    full_Yago2 = CoreTriplesFactory(ten,num_entities=len(entity_to_id_map),num_relations=len(relation_to_id_map))
    h = Dataset().from_tf(full_Yago2, [0.8,0.1,0.1])

    storeTriplesYago(f'approach/KFold/Yago2_5_fold/training', h.training.mapped_triples.tolist())
    storeTriplesYago(f'approach/KFold/Yago2_5_fold/testing', h.testing.mapped_triples.tolist())
    storeTriplesYago(f'approach/KFold/Yago2_5_fold/validation', h.validation.mapped_triples.tolist())

    dh.generateKFoldSplit(ten, 'Yago2', random_seed=None, n_split=nmb_KFold)

    emb_triples_id, LP_triples_id = dh.loadKFoldSplit(0, 'Yago2',n_split=nmb_KFold)
    emb_triples = ten[emb_triples_id]
    LP_triples = ten[LP_triples_id]
    
    emb_train_triples = CoreTriplesFactory(emb_triples,num_entities=len(entity_to_id_map),num_relations=len(relation_to_id_map))
    emb_test_triples = CoreTriplesFactory(LP_triples,num_entities=len(entity_to_id_map),num_relations=len(relation_to_id_map))
    del ten
    del emb_triples
    del LP_triples
    gc.collect()
    torch.cuda.empty_cache()

    result = pipeline(training=emb_train_triples,testing=emb_test_triples,model=TransE,random_seed=4,training_loop='sLCWA', model_kwargs=dict(embedding_dim=50),training_kwargs=dict(num_epochs=50), evaluation_fallback= True, device='cuda:5')   

    #model = result.model

    result.save_to_directory(f"approach/trainedEmbeddings/Yago2")

def findingRankNegHead(orderedList, key, all_triples_set, fix):
    '''
    Helper function to find rank of triple with fixed head
    '''
    counter = 1
    for ele in orderedList:
        if key[0] == ele[0] and key[1] == ele[1]:
            return counter
        tup = (fix,ele[0],ele[1])
        if tup in all_triples_set:
            continue
        counter += 1
    return None

def findingRankNegTail(orderedList, key, all_triples_set, fix):
    '''
    Helper function to find rank of triple with fixed tail
    '''
    counter = 1
    for ele in orderedList:
        if key[0] == ele[0] and key[1] == ele[1]:
            return counter
        tup = (ele[0],ele[1],fix)
        if tup in all_triples_set:
            continue
        counter += 1
    return None

def findingRankNegHead_Yago(orderedList, key, all_triples_set, fix, map, map_r):
    '''
    Helper function to find rank of triple with fixed head on the yago2 dataset
    '''
    counter = 1
    for ele in orderedList:
        if key[0] == ele[0] and key[1] == ele[1]:
            return counter
        tup = (map[fix],map_r[ele[0]],map[ele[1]])
        if tup in all_triples_set:
            continue
        counter += 1
    return None

def findingRankNegTail_Yago(orderedList, key, all_triples_set, fix, map, map_r):
    '''
    Helper function to find rank of triple with fixed tail on the yago2 dataset
    '''
    counter = 1
    for ele in orderedList:
        if key[0] == ele[0] and key[1] == ele[1]:
            return counter
        tup = (map[ele[0]],map_r[ele[1]],map[fix])
        if tup in all_triples_set:
            continue
        counter += 1
    return None

def getReliKScore(u: str, v: str, M: nx.MultiDiGraph, models: list[object], entity_to_id_map: object, relation_to_id_map: object, all_triples_set: set[tuple[int,int,int]], alltriples: TriplesFactory, samples: float, dataset: str) -> float:
    '''
    get exact ReliK score
    '''
    global getkHopneighbors_time
    global entity_relation_loop_time
    global model_loop_time

    start_time = timeit.default_timer() #profiling 1
    subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(u,v,M)
    end_time = timeit.default_timer()
    getkHopneighbors_time += end_time - start_time #profiling 1

    if dataset == 'Yago2':
        head = u
        tail = v
    else:
        head = entity_to_id_map[u]
        tail = entity_to_id_map[v]

    first_u = True
    first_v = True

    start_time = timeit.default_timer() #profiling 2
    for ent in range(alltriples.num_entities):
        for rel in range(alltriples.num_relations):
            kg_neg_triple_tuple = (entity_to_id_map[u],rel,ent)
            if kg_neg_triple_tuple not in all_triples_set:
                if first_u:
                    first_u = False
                    rslt_torch_u = torch.LongTensor([entity_to_id_map[u],rel,ent])
                    rslt_torch_u = rslt_torch_u.resize_(1,3)
                else:
                    rslt_torch_u = torch.cat((rslt_torch_u, torch.LongTensor([entity_to_id_map[u],rel,ent]).resize_(1,3)))

            kg_neg_triple_tuple = (ent,rel,entity_to_id_map[v])
            if kg_neg_triple_tuple not in all_triples_set:
                if first_v:
                    first_v = False
                    rslt_torch_v = torch.LongTensor([ent,rel,entity_to_id_map[v]])
                    rslt_torch_v = rslt_torch_v.resize_(1,3)
                else:
                    rslt_torch_v = torch.cat((rslt_torch_v, torch.LongTensor([ent,rel,entity_to_id_map[v]]).resize_(1,3)))
    
    end_time = timeit.default_timer()
    entity_relation_loop_time += end_time - start_time #profiling 2

    first = True
    for tp in list(existing):
        if first:
            first = False
            ex_torch = torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]])
            ex_torch = ex_torch.resize_(1,3)
        else:
            ex_torch = torch.cat((ex_torch, torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]]).resize_(1,3)))

    hRankNeg = 0
    tRankNeg = 0

    start_time = timeit.default_timer() #profiling 3
    for i in range(len(models)):
        # make sure the model is on GPU
        #models[i].to('cuda')

        comp_score = models[i].score_hrt(ex_torch).cpu()
        rslt_u_score = models[i].score_hrt(rslt_torch_u)
        rslt_v_score = models[i].score_hrt(rslt_torch_v)
        count = 0
        he_sc = 0
        ta_sc = 0
        for tr in comp_score:
            count += 1
            he_sc += torch.sum(rslt_u_score > tr).detach().numpy() + 1
            ta_sc += torch.sum(rslt_v_score > tr).detach().numpy() + 1
        hRankNeg += (he_sc /len(models))
        tRankNeg += (ta_sc /len(models))
    
    end_time = timeit.default_timer()
    model_loop_time += end_time - start_time #profiling 3
    # print(getkHopneighbors_time, entity_relation_loop_time, model_loop_time)
    
    return ( (1/hRankNeg) + (1/tRankNeg) ) /2, 1/hRankNeg, 1/tRankNeg


# We need a more fine grained profiling to understand the bottlenecks in sampling
random_choice_time = 0.0


def binomial(u: str, v: str, M: nx.MultiDiGraph, models: list[object], entity_to_id_map: object, relation_to_id_map: object, all_triples_set: set[tuple[int,int,int]], num_entities: int, num_relations: int, sample: float, dataset: str) -> float:
    '''
    get approximate ReliK score with binomial approximation
    '''
    #list(map(random.choice, map(list, list_of_sets)))

    global getkHopneighbors_time
    global sample_time
    global model_loop_time
    global sample_first_while_time
    global sample_second_while_time

    start_time = timeit.default_timer() # profiling 1
    subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(u,v,M)
    end_time = timeit.default_timer() # profiling 1
    getkHopneighbors_time += end_time - start_time # profiling 1

    start_time = timeit.default_timer() # profiling 2
    #print(entity_to_id_map)
    #subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(entity_to_id_map[u],entity_to_id_map[v],M)
    if sample > 0.4:
        allset_uu = set(itertools.product([entity_to_id_map[u]],range(num_relations),range(num_entities)))
        allset_vv = set(itertools.product(range(num_entities),range(num_relations),[entity_to_id_map[v]]))
        len_uu = len(allset_uu.difference(all_triples_set))
        len_vv = len(allset_vv.difference(all_triples_set))

        allset = set()
        allset_u = set()
        allset_v = set()
        
        lst_emb = list(range(num_entities))
        lst_emb_r = list(range(num_relations))

        first = True
        count = 0
        while len(allset_u) < len_uu * sample:
        #while len(allset_u) < min(len_uu*sample,1000):
            #count += 1
            relation = random.choice(lst_emb_r)
            tail = random.choice(lst_emb)
            kg_neg_triple_tuple = (entity_to_id_map[u],relation,tail)
            if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_u:
                if first:
                    first = False
                    rslt_torch_u = torch.LongTensor([entity_to_id_map[u],relation,tail])
                    rslt_torch_u = rslt_torch_u.resize_(1,3)
                else:
                    rslt_torch_u = torch.cat((rslt_torch_u, torch.LongTensor([entity_to_id_map[u],relation,tail]).resize_(1,3)))
                allset_u.add(kg_neg_triple_tuple)
            else:
                count += 1
            #if count == len_uu*sample:#min(len_uu*sample,1000):
            #    break
        count = 0
        first = True
        while len(allset_v) < len_vv * sample:
        #while len(allset_v) < min(len_vv*sample,1000):
            #count += 1
            relation = random.choice(lst_emb_r)
            head = random.choice(lst_emb)
            kg_neg_triple_tuple = (head,relation,entity_to_id_map[v])
            if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_v:
                if first:
                    first = False
                    rslt_torch_v = torch.LongTensor([head,relation,entity_to_id_map[v]])
                    rslt_torch_v = rslt_torch_v.resize_(1,3)
                else:
                    rslt_torch_v = torch.cat((rslt_torch_v, torch.LongTensor([head,relation,entity_to_id_map[v]]).resize_(1,3)))
                allset_v.add(kg_neg_triple_tuple)
            else:
                count += 1
    else:
        allset_u = set()
        allset_v = set()
        len_uu = num_entities*num_relations
        len_vv = num_entities*num_relations
        first = True
        start_first_while = timeit.default_timer() # profiling 4
        
        while len(allset_u) < len_uu * sample:
        #while len(allset_u) < min(len_uu*sample,1000):

            kg_neg_triple_tuple = tuple(map(random.choice, map(list, [range(num_relations),range(num_entities)] )))
            
            # optimization: sampling without replacement
            kg_neg_triple_tuple = (entity_to_id_map[u], kg_neg_triple_tuple[0], kg_neg_triple_tuple[1])
            
            if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_u:
                if first:
                    first = False
                    rslt_torch_u = torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]])
                    rslt_torch_u = rslt_torch_u.resize_(1,3)
                else:
                    rslt_torch_u = torch.cat((rslt_torch_u, torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]]).resize_(1,3)))
                allset_u.add(kg_neg_triple_tuple)
        #print(len(rslt_torch_u))

        end_first_while = timeit.default_timer() # profiling 4
        sample_first_while_time += end_first_while - start_first_while # profiling 4

        first = True

        start_second_while = timeit.default_timer() # profiling 5

        while len(allset_v) < len_vv * sample:
        #while len(allset_v) < min(len_vv*sample,1000):
            kg_neg_triple_tuple = tuple(map(random.choice, map(list, [range(num_relations),range(num_entities)] )))
            kg_neg_triple_tuple = (kg_neg_triple_tuple[1], kg_neg_triple_tuple[0], entity_to_id_map[v])
            if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_v:
                if first:
                    first = False
                    rslt_torch_v = torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]])
                    rslt_torch_v = rslt_torch_v.resize_(1,3)
                else:
                    rslt_torch_v = torch.cat((rslt_torch_v, torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]]).resize_(1,3)))
                allset_v.add(kg_neg_triple_tuple)
        

        end_second_while = timeit.default_timer() # profiling 5
        sample_second_while_time += end_second_while - start_second_while # profiling 5

    #print(len(rslt_torch_u))
    #print(rslt_torch_v)

    end_time = timeit.default_timer() # profiling 2
    sample_time += end_time - start_time # profiling 2

    first = True
    for tp in list(existing):
        if first:
            first = False
            ex_torch = torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]])
            ex_torch = ex_torch.resize_(1,3)
        else:
            ex_torch = torch.cat((ex_torch, torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]]).resize_(1,3)))

    hRankNeg = 0
    tRankNeg = 0
    #print(f"Here: {os.getpid()}")
    start_time = timeit.default_timer() # profiling 3
    for i in range(len(models)):
        comp_score = models[i].score_hrt(ex_torch).cpu()
        rslt_u_score = models[i].score_hrt(rslt_torch_u)
        rslt_v_score = models[i].score_hrt(rslt_torch_v)
        count = 0
        he_sc = 0
        ta_sc = 0
        for tr in comp_score:
            count += 1
            he_sc += torch.sum(rslt_u_score > tr).detach().numpy() + 1
            ta_sc += torch.sum(rslt_v_score > tr).detach().numpy() + 1
        hRankNeg += ((he_sc / len(allset_u))/len(models)) * len_uu
        tRankNeg += ((ta_sc / len(allset_v))/len(models)) * len_vv
    end_time = timeit.default_timer() # profiling 3
    model_loop_time += end_time - start_time # profiling 3

    #print(( 1/hRankNeg + 1/tRankNeg )/2, 1/hRankNeg, 1/tRankNeg)
    return ( 1/hRankNeg + 1/tRankNeg )/2, 1/hRankNeg, 1/tRankNeg

# this is the optimized version of the binomial approximation

def pre_randperm(num_entities: int, num_relations: int, device='cuda') -> torch.Tensor:
    perm_entities = torch.randperm(num_entities, device=device)
    perm_relations = torch.randperm(num_relations, device=device)
    return perm_entities, perm_relations

perm_entities = None
perm_relations = None
all_triple_id_torch = None

#sampled_indices_only_once = None

def encode_triples_to_id(triples, entity_count: int, relation_count: int, device='cuda') -> torch.tensor:
    entity1, relation, entity2 = triples[:, 0], triples[:, 1], triples[:, 2]
    return entity1 * relation_count * entity_count + relation * entity_count + entity2

def decode_id_to_tensor(encoded_ids, entity_count: int, relation_count: int, device='cuda') -> torch.tensor:
    entity1 = encoded_ids // (relation_count * entity_count)
    remaining = encoded_ids % (relation_count * entity_count)
    relation = remaining // entity_count
    entity2 = remaining % entity_count
    triple_tensor = torch.stack((entity1, relation, entity2), dim=-1)
    return triple_tensor

def binomial_cuda(u: str, v: str, M: nx.MultiDiGraph, models: list[object], entity_to_id_map: object, 
             relation_to_id_map: object, all_triples_set: set[tuple[int,int,int]], 
             num_entities: int, num_relations: int, sample: float, dataset: str, device='cuda', this_perm_entities=None, this_perm_relations=None, all_triple_id_torch = None) -> float:
    '''
    Get approximate ReliK score with binomial approximation (optimized sampling)
    '''
    #list(map(random.choice, map(list, list_of_sets)))

    global getkHopneighbors_time
    global sample_time
    global model_loop_time
    global sample_first_while_time
    global sample_second_while_time

    start_time = timeit.default_timer() # profiling 1
    subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(u,v,M)
    end_time = timeit.default_timer() # profiling 1
    getkHopneighbors_time += end_time - start_time # profiling 1

    start_time = timeit.default_timer() # profiling 2
    #print(entity_to_id_map)
    #subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(entity_to_id_map[u],entity_to_id_map[v],M)
    if sample >= 0:
        allset_u = set()
        allset_v = set()
        len_uu = num_entities*num_relations
        len_vv = num_entities*num_relations
        first = True
        start_first_while = timeit.default_timer() # profiling 4
        """
        while len(allset_u) < min(len_uu*sample,1000):
            kg_neg_triple_tuple = tuple(map(random.choice, map(list, [range(alltriples.num_relations),range(alltriples.num_entities)] )))
            
            # optimization: sampling without replacement
            kg_neg_triple_tuple = (entity_to_id_map[u], kg_neg_triple_tuple[0], kg_neg_triple_tuple[1])
            
            if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_u:
                if first:
                    first = False
                    rslt_torch_u = torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]])
                    rslt_torch_u = rslt_torch_u.resize_(1,3)
                else:
                    rslt_torch_u = torch.cat((rslt_torch_u, torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]]).resize_(1,3)))
                allset_u.add(kg_neg_triple_tuple)
        """
        # Directly sample 20% more indices without checking for negativities
        # Also assume there is no duplicates in sampling_tensor

        target_length = int(len_uu * sample * 1.2)

        entity_repeats = (target_length + len(this_perm_entities) - 1) // len(this_perm_entities)
        relation_repeats = (target_length + len(this_perm_relations) - 1) // len(this_perm_relations)
        
        head_cycle = torch.full((target_length,), entity_to_id_map[u], device=device)
        entity_cycle = this_perm_entities.repeat(entity_repeats)[:target_length]
        relation_cycle = this_perm_relations.repeat(relation_repeats)[:target_length]

        # stack the indices
        sampling_tensor = torch.stack([head_cycle, relation_cycle, entity_cycle], dim=1)

        sampling_triple_id = encode_triples_to_id(sampling_tensor, num_entities, num_relations, device='cuda')


        #compareview = all_triple_id_torch.repeat(sampling_triple_id.shape[0], 1).T.to(device)

        #print(sampling_triple_id.device, all_triple_id_torch.device)

        # Non-intersection (sampling except positive)
        
        only_negative_triples = sampling_triple_id[~torch.isin(sampling_triple_id, all_triple_id_torch)].clone().detach()

        allset_u = decode_id_to_tensor(only_negative_triples, num_entities, num_relations)

        #print(len(rslt_torch_u))
        #print(rslt_torch_u[:5])

        end_first_while = timeit.default_timer() # profiling 4
        sample_first_while_time += end_first_while - start_first_while # profiling 4

        first = True

        start_second_while = timeit.default_timer() # profiling 5
        """
        while len(allset_v) < min(len_vv*sample,1000):
            kg_neg_triple_tuple = tuple(map(random.choice, map(list, [range(alltriples.num_relations),range(alltriples.num_entities)] )))
            kg_neg_triple_tuple = (kg_neg_triple_tuple[1], kg_neg_triple_tuple[0], entity_to_id_map[v])
            if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_u:
                if first:
                    first = False
                    rslt_torch_v = torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]])
                    rslt_torch_v = rslt_torch_v.resize_(1,3)
                else:
                    rslt_torch_v = torch.cat((rslt_torch_v, torch.LongTensor([kg_neg_triple_tuple[0],kg_neg_triple_tuple[1],kg_neg_triple_tuple[2]]).resize_(1,3)))
                allset_v.add(kg_neg_triple_tuple)
        """
        # Directly sample 20% more indices without checking for negativities
        # Also assume there is no duplicates in sampling_tensor
        
        target_length = int(len_vv * sample * 1.2)
        #target_length = int(min(len_vv*sample, 1000) * 1.2)
        #global perm_relations, perm_entities, all_triple_id_torch

        # WARNING: Assuming that the number of entities and relations is relatively prime
        entity_repeats = (target_length + len(this_perm_entities) - 1) // len(this_perm_entities)
        relation_repeats = (target_length + len(this_perm_relations) - 1) // len(this_perm_relations)

        tail_cycle = torch.full((target_length,), entity_to_id_map[v], device=device)
        entity_cycle = this_perm_entities.repeat(entity_repeats)[:target_length]
        relation_cycle = this_perm_relations.repeat(relation_repeats)[:target_length]

        # stack the indices
        sampling_tensor = torch.stack([entity_cycle, relation_cycle, tail_cycle], dim=1)

        sampling_triple_id = encode_triples_to_id(sampling_tensor, num_entities, num_relations, device='cuda')

        # Non-intersection (sampling except positive)

        only_negative_triples = sampling_triple_id[~torch.isin(sampling_triple_id, all_triple_id_torch)].clone().detach()

        allset_v = decode_id_to_tensor(only_negative_triples, num_entities, num_relations)

        end_second_while = timeit.default_timer() # profiling 5
        sample_second_while_time += end_second_while - start_second_while # profiling 5

    rslt_torch_u = allset_u
    rslt_torch_v = allset_v

    end_time = timeit.default_timer() # profiling 2
    sample_time += end_time - start_time # profiling 2

    first = True
    for tp in list(existing):
        if first:
            first = False
            ex_torch = torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]])
            ex_torch = ex_torch.resize_(1,3)
        else:
            ex_torch = torch.cat((ex_torch, torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]]).resize_(1,3)))

    hRankNeg = 0
    tRankNeg = 0


    start_time = timeit.default_timer() # profiling 3
    for i in range(len(models)):
        comp_score = models[i].score_hrt(ex_torch).cpu()
        rslt_u_score = models[i].score_hrt(rslt_torch_u)
        rslt_v_score = models[i].score_hrt(rslt_torch_v)
        count = 0
        he_sc = 0
        ta_sc = 0
        for tr in comp_score:
            count += 1
            he_sc += torch.sum(rslt_u_score > tr).detach().numpy() + 1
            ta_sc += torch.sum(rslt_v_score > tr).detach().numpy() + 1
        hRankNeg += ((he_sc / len(allset_u))/len(models)) * len_uu
        tRankNeg += ((ta_sc / len(allset_v))/len(models)) * len_vv
    end_time = timeit.default_timer() # profiling 3
    model_loop_time += end_time - start_time # profiling 3

    #print(( 1/hRankNeg + 1/tRankNeg )/2, 1/hRankNeg, 1/tRankNeg)
    return ( 1/hRankNeg + 1/tRankNeg )/2, 1/hRankNeg, 1/tRankNeg

def lower_bound(u: str, v: str, M: nx.MultiDiGraph, models: list[object], entity_to_id_map: object, relation_to_id_map: object, all_triples_set: set[tuple[int,int,int]], alltriples: TriplesFactory, sample: float, dataset: str) -> float:
    '''
    get approximate ReliK score with lower bound approximation
    '''
    subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(u,v,M)

    allset_uu = set(itertools.product([entity_to_id_map[u]],range(alltriples.num_relations),range(alltriples.num_entities)))
    allset_vv = set(itertools.product(range(alltriples.num_entities),range(alltriples.num_relations),[entity_to_id_map[v]]))

    len_uu = len(allset_uu.difference(all_triples_set))
    len_vv = len(allset_vv.difference(all_triples_set))

    allset_u = set()
    allset_v = set()

    lst_emb = list(range(alltriples.num_entities))
    lst_emb_r = list(range(alltriples.num_relations))
    
    first = True
    count = 0
    while len(allset_u) < len_uu*sample:
        #count += 1
        relation = random.choice(lst_emb_r)
        tail = random.choice(lst_emb)
        kg_neg_triple_tuple = (entity_to_id_map[u],relation,tail)
        if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_u:
            if first:
                first = False
                rslt_torch_u = torch.LongTensor([entity_to_id_map[u],relation,tail])
                rslt_torch_u = rslt_torch_u.resize_(1,3)
            else:
                rslt_torch_u = torch.cat((rslt_torch_u, torch.LongTensor([entity_to_id_map[u],relation,tail]).resize_(1,3)))
            allset_u.add(kg_neg_triple_tuple)
        else:
            count += 1
        #if count == max_limit*2:
            #break

    count = 0
    first = True
    while len(allset_v) < len_vv*sample:
        #count += 1
        relation = random.choice(lst_emb_r)
        head = random.choice(lst_emb)
        kg_neg_triple_tuple = (head,relation,entity_to_id_map[v])
        if kg_neg_triple_tuple not in all_triples_set and kg_neg_triple_tuple not in allset_v:
            if first:
                first = False
                rslt_torch_v = torch.LongTensor([head,relation,entity_to_id_map[v]])
                rslt_torch_v = rslt_torch_v.resize_(1,3)
            else:
                rslt_torch_v = torch.cat((rslt_torch_v, torch.LongTensor([head,relation,entity_to_id_map[v]]).resize_(1,3)))
            allset_v.add(kg_neg_triple_tuple)
        else:
            count += 1
        #if count == max_limit*2:
            #break

    #print(rslt_torch_v)
    allset = allset_u.union(allset_v)
    selectedComparators = allset

    first = True
    for tp in list(existing):
        if first:
            first = False
            ex_torch = torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]])
            ex_torch = ex_torch.resize_(1,3)
        else:
            ex_torch = torch.cat((ex_torch, torch.LongTensor([entity_to_id_map[u],relation_to_id_map[tp],entity_to_id_map[v]]).resize_(1,3)))

    hRankNeg = 0
    tRankNeg = 0
    for i in range(len(models)):
        comp_score = models[i].score_hrt(ex_torch).cpu()
        rslt_u_score = models[i].score_hrt(rslt_torch_u)
        rslt_v_score = models[i].score_hrt(rslt_torch_v)
        count = 0
        he_sc = 0
        ta_sc = 0
        for tr in comp_score:
            count += 1
            he_sc += torch.sum(rslt_u_score > tr).detach().numpy() + 1
            ta_sc += torch.sum(rslt_v_score > tr).detach().numpy() + 1
        hRankNeg += (he_sc + len_uu - len(allset_u))/len(models)
        tRankNeg += (ta_sc + len_vv - len(allset_v))/len(models)

    return ( 1/hRankNeg + 1/tRankNeg )/2

def densestSubgraph(datasetname, embedding, score_calculation, sample, models):
    '''
    compute and store the weights with chosen score approximation for densest subgraphs tests
    '''
    path = f"approach/KFold/{datasetname}_{5}_fold/{embedding}_weightedGraph_{score_calculation.__name__}_{sample}_samples.csv"
    isExist = os.path.exists(path)
    if False:
        G = nx.Graph()
        with open(f"approach/KFold/{datasetname}_{5}_fold/{embedding}_weightedGraph_{score_calculation.__name__}_{sample}_samples.csv", "r") as f:
            plots = csv.reader(f, delimiter=',')
            for row in plots:
                G.add_edge(str(row[0]),str(row[1]),weight=float(row[2]))
    else:
        if datasetname == 'Yago2':
            data=pd.read_csv('approach/Yago2core_facts.clean.notypes_3.tsv',sep='\t',names=['subject', 'predicate', 'object'])

            entity_to_id_map = {v: k for v, k in enumerate(pd.factorize(pd.concat([data['subject'],data['object']]))[1])}
            entity_to_id_map2 = {k: v for v, k in enumerate(pd.factorize(pd.concat([data['subject'],data['object']]))[1])}
            relation_to_id_map = {v: k for v, k in enumerate(pd.factorize(data['predicate'])[1])}
            relation_to_id_map2 = {k: v for v, k in enumerate(pd.factorize(data['predicate'])[1])}
            data['subject'] = data['subject'].map(entity_to_id_map2)
            data['object'] = data['object'].map(entity_to_id_map2)  
            data['predicate'] = data['predicate'].map(relation_to_id_map2)
            ten = torch.tensor(data.values)

            full_graph = CoreTriplesFactory(ten,num_entities=len(entity_to_id_map),num_relations=len(relation_to_id_map))
            df = pd.DataFrame(full_graph.mapped_triples, columns=['subject', 'predicate', 'object'])
            all_triples_set = set[tuple[int,int,int]]()
            for tup in full_graph.mapped_triples.tolist():
                all_triples_set.add((tup[0],tup[1],tup[2]))
        else:
            all_triples, all_triples_set, entity_to_id_map, relation_to_id_map, test_triples, validation_triples = emb.getDataFromPykeen(datasetname=datasetname)
            full_dataset = torch.cat((all_triples, test_triples.mapped_triples, validation_triples.mapped_triples))
            full_graph = TriplesFactory(full_dataset,entity_to_id=entity_to_id_map,relation_to_id=relation_to_id_map)
            df = pd.DataFrame(full_graph.triples, columns=['subject', 'predicate', 'object'])
        M = nx.MultiDiGraph()

        for t in df.values:
            M.add_edge(t[0], t[2], label = t[1])

        G = nx.Graph()
        count = 0
        pct = 0
        start = timeit.default_timer()
        length: int = len(nx.DiGraph(M).edges())
        print(f'Starting with {length}')
        if parallel_uv is True:
            perm_relations, perm_entities = pre_randperm(full_graph.num_entities, full_graph.num_relations)
        for u,v in nx.DiGraph(M).edges():
            w,tailRR,relationRR = score_calculation(u, v, M, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, sample, datasetname)
            if G.has_edge(u,v):
                G[u][v]['weight'] += w
                G[u][v]['tailRR'] += tailRR
                G[u][v]['relationRR'] += relationRR
            else:
                G.add_edge(u, v, weight=w)
                G.add_edge(u, v, tailRR=tailRR)
                G.add_edge(u, v, relationRR=relationRR)
            count += 1
            now = timeit.default_timer()
            if count % ((length // 100)+1) == 0:
                pct += 1
                now = timeit.default_timer()
                print(f'Finished with {pct}% for {datasetname} in time {now-start}, took avg of {(now-start)/pct} per point')
            #if(pct == 5):
            #    break

        weighted_graph: list[tuple[str,str,float]] = []
        for u,v,data in G.edges(data=True):
            weighted_graph.append((u,v,data['weight'],data['tailRR'],data['relationRR']))

        with open(f"approach/KFold/{datasetname}_{5}_fold/{embedding}_weightedGraph_{score_calculation.__name__}_{sample}_samples.csv", "w") as f:
            wr = csv.writer(f)
            wr.writerows(weighted_graph)

def RR(u: str, v: str, M: nx.MultiDiGraph, models: list[object], entity_to_id_map: object, relation_to_id_map: object, all_triples_set: set[tuple[int,int,int]], alltriples: TriplesFactory, sample: float, dataset: str) -> float:
    '''
    get reciprocal rank scores
    '''
    subgraph_list, labels, existing, count, ex_triples  = dh.getkHopneighbors(u,v,M)
    head = entity_to_id_map[u]
    tail = entity_to_id_map[v]
    list_relation = torch.tensor([[head,i,tail] for i in range(models[0].num_relations) if (head,i, tail) not in all_triples_set ])

    first = True
    for tp in list(existing):
        if first:
            first = False
            print([u,tp,v])
            ex_torch = torch.LongTensor([head,relation_to_id_map[tp],tail])
            ex_torch = ex_torch.resize_(1,3)
            list_tail = [torch.tensor([[head,relation_to_id_map[tp],i] for i in range(models[0].num_entities) if (head,relation_to_id_map[tp], i) not in all_triples_set ])]
        else:
            ex_torch = torch.cat((ex_torch, torch.LongTensor([head,relation_to_id_map[tp],tail]).resize_(1,3)))
            list_tail = list_tail + [torch.tensor([[head,relation_to_id_map[tp],i] for i in range(models[0].num_entities) if (head,relation_to_id_map[tp], i) not in all_triples_set ])]
    hRankNeg = 0.
    tRankNeg = 0.
    for i in range(len(models)):
        comp_score = models[i].score_hrt(ex_torch).cpu()
        
        rslt_v_score = models[i].score_hrt(list_relation).cpu()
        count = 0
        he_sc = 0
        ta_sc = 0
        for tr in comp_score:
            rslt_u_score = models[i].score_hrt(list_tail[count]).cpu()
            count += 1
            he_sc += torch.sum(rslt_u_score > tr).detach().numpy() + 1
            ta_sc += torch.sum(rslt_v_score > tr).detach().numpy() + 1
        hRankNeg += he_sc / len(models)
        tRankNeg += ta_sc / len(models)

    return ( 1/hRankNeg + 1/tRankNeg )/2, 1/hRankNeg, 1/tRankNeg

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-e','--embedding', dest='embedding', type=str, help='choose which embedding type to use')
    parser.add_argument('-d','--datasetname', dest='dataset_name', type=str, help='choose which dataset to use')
    parser.add_argument('-t','--tasks', dest='tasks', type=str, help='if set, only run respective tasks, split with \",\" could be from [relik, prediction, triple, densest]')
    parser.add_argument('-s','--subgraphs', dest='size_subgraphs', type=int, help='choose which size of subgraphs are getting tested')
    parser.add_argument('-n','--n_subgraphs', dest='n_subgraphs', type=int, help='choose which n of subgraphs are getting tested')
    parser.add_argument('-st','--setup', dest='setup',action='store_true' ,help='if set, just setup and train embedding, subgraphs, neg-triples')
    parser.add_argument('-heur','--heuristic', dest='heuristic', type=str, help='which heuristic should be used in the case of dense subgraph task')
    parser.add_argument('-r','--ratio', dest='ratio', type=str, help='how much should be sampled for binomial', default=0.1)
    parser.add_argument('-c','--class', dest='classifier', type=str, help='classifier type')
    args = parser.parse_args()

    nmb_KFold: int = 5

    if args.embedding == 'Yago':
        Yago2()
        quit()

    # Default cases in which return message to user, that some arguments are needed
    if not args.heuristic and not args.setup and not args.size_subgraphs and not args.tasks and not args.dataset_name and not args.embedding:
        print('Please, provide at least an embedding and a dataset to perform an experiment')
        quit(code=1)

    if not args.dataset_name and not args.embedding:
        print('Please, provide at least an embedding and a dataset to perform an experiment')    
        quit(code=1)

    if not args.dataset_name:
        print('Please, provide at least a dataset to perform an experiment')    
        quit(code=1)

    if not args.embedding:
        print('Please, provide at least an embedding to perform an experiment')    
        quit(code=1)

    # If no list provided do everything
    if args.tasks:
        task_list: set[str] = set(args.tasks.split(','))
    elif args.setup:
        task_list: set[str] = set()
    else:
        task_list: set[str] = set(('relik', 'prediction', 'triple', 'densest'))

    if args.size_subgraphs:
        size_subgraphs = args.size_subgraphs
    else:
        size_subgraphs = 50

    if args.n_subgraphs:
        n_subgraphs = args.n_subgraphs
    else:
        n_subgraphs = 500
    if 'ReliK' in task_list:
        ratio = float(args.ratio)
    if args.heuristic:
        heuristic = args.heuristic
        if heuristic == 'binomial':
            ratio = float(args.ratio)
            heuristic = binomial
        if heuristic == 'relik':
            heuristic = getReliKScore
            ratio = 0.1
        if heuristic == 'lower':
            heuristic = lower_bound
        if heuristic == 'RR':
            heuristic = RR
        # add test for cuda optimization for binomial
        if heuristic == 'binomial-cuda':
            heuristic = binomial_cuda
            ratio = 0.1
    else:
        heuristic = binomial
        ratio = 0.1
    if args.ratio:
        ratio = float(args.ratio)
    if args.dataset_name == 'Countries':
        device = 'cuda:0'
    if args.dataset_name == 'CodexSmall':
        device = 'cuda:4'
    if args.dataset_name == 'CodexMedium':
        device = 'cuda:2'
    if args.dataset_name == 'CodexLarge':
        device = 'cuda:3'
    if args.dataset_name == 'FB15k237':
        device = 'cuda:4'
    if args.dataset_name == 'FB15k':
        device = 'cuda:6'

    if not args.classifier:
        classifier = 'LogisticRegression'
    else:
        classifier = args.classifier
    '''if torch.has_mps:
        device = 'mps'''
    device = 'cpu'
    #print(heuristic)
    path = f"approach/scoreData/{args.dataset_name}_{nmb_KFold}/{args.embedding}"
    isExist = os.path.exists(path)
    if not isExist:
        os.makedirs(path)

    if args.dataset_name != 'Yago2':
        # collecting all information except the model from the KFold
        all_triples, all_triples_set, entity_to_id_map, relation_to_id_map, emb_train_triples, emb_test_triples, LP_triples_pos, full_graph = grabAllKFold(args.dataset_name, nmb_KFold, args.embedding)

        # checking if we have negative triples for
        LP_triples_neg = KFoldNegGen(args.dataset_name, nmb_KFold, all_triples_set, LP_triples_pos, emb_train_triples)

        # getting or training the models
        models = getOrTrainModels(args.embedding, args.dataset_name, nmb_KFold, emb_train_triples, emb_test_triples, device)

        if not os.path.isfile(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold/subgraphs_{size_subgraphs}.csv"):
            subgraphs = dh.createSubGraphs(all_triples, entity_to_id_map, relation_to_id_map, number_of_graphs=n_subgraphs, size_of_graphs=size_subgraphs)
            dh.storeSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", subgraphs)
        else:
            subgraphs = dh.loadSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", size_subgraphs)
            if len(subgraphs) < n_subgraphs:
                    subgraphs_new = dh.createSubGraphs(all_triples, entity_to_id_map, relation_to_id_map, size_of_graphs=size_subgraphs, number_of_graphs=(n_subgraphs-len(subgraphs)))
                    dh.storeSubGraphs(f"approach/KFold/{args.dataset_name}_{nmb_KFold}_fold", subgraphs_new)
                    subgraphs = subgraphs + subgraphs_new
            if len(subgraphs) > n_subgraphs:
                    subgraphs = subgraphs[:n_subgraphs]
    else:
        models = [emb.loadModel(f"Yago2",'mps')]

    if parallel_uv == True:
        perm_entities, perm_relations = pre_randperm(full_graph.num_entities, full_graph.num_relations)


    tstamp_sib = -1
    tstamp_pre = -1
    tstamp_tpc = -1
    tstamp_den = -1

    if 'time-measure' in task_list:
        print('start with time measure')

        path = f"approach/scoreData/time_measures_{args.embedding}_{args.dataset_name}_{args.heuristic}_approx.csv"
        ex = os.path.isfile(path)
        c = open(f'{path}', "a+")
        writer = csv.writer(c)
        """
        start = timeit.default_timer()
        densestSubgraph(args.dataset_name, args.embedding, getReliKScore, ratio, models)
        end = timeit.default_timer()
        data = ['accurate', ratio, end-start]
        writer.writerow(data)
        """
        for rat in [0.05,0.1,0.15,0.2,0.25,0.3,0.35,0.4,0.45,0.5,0.55,0.6,0.65,0.7,0.75,0.8,0.85,0.9,0.95,1.0]:
        #for rat in [0.05,0.1,0.15,0.2,0.25,0.3,0.35,0.4,0.45,0.5]:
            ratio = rat
            print(f'sampling ratio = {ratio}')
            start = timeit.default_timer()
            # DoGlobalReliKScore(args.embedding, args.dataset_name, nmb_KFold, size_subgraphs, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, ratio, heuristic)
            if args.heuristic == 'binomial-cuda':
                perm_entities, perm_relations = pre_randperm(full_graph.num_entities, full_graph.num_relations)
            densestSubgraph(args.dataset_name, args.embedding, heuristic, ratio, models)
            end = timeit.default_timer()
            data = [f'{args.heuristic}', ratio, end-start]
            print(data)
            writer.writerow(data)
            """
            start = timeit.default_timer()
            densestSubgraph(args.dataset_name, args.embedding, binomial, ratio, models)
            end = timeit.default_timer()
            data = ['binomial', ratio, end-start]
            writer.writerow(data)
            """
        c.close()
        exit()

        print('end with time measure')
    if 'ReliK' in task_list:
        print('start with ReliK')
        start = timeit.default_timer()
        DoGlobalReliKScore(args.embedding, args.dataset_name, nmb_KFold, size_subgraphs, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, ratio, heuristic)
        end = timeit.default_timer()
        print('end with ReliK')
        tstamp_sib = end - start
    if 'prediction' in task_list:
        print('start with prediction')
        start = timeit.default_timer()
        prediction(args.embedding, args.dataset_name, size_subgraphs, emb_train_triples, all_triples_set, nmb_KFold)
        end = timeit.default_timer()
        print('end with prediction')
        tstamp_pre = end - start
    if 'triple' in task_list:
        print('start with triple')
        entity2embedding, relation2embedding = None, None
        start = timeit.default_timer()
        classifierExp(args.embedding, args.dataset_name, size_subgraphs, LP_triples_pos,  LP_triples_neg, entity2embedding, relation2embedding, emb_train_triples, nmb_KFold, models, entity_to_id_map, relation_to_id_map, classifier)
        end = timeit.default_timer()
        print('end with triple')
        tstamp_tpc = end - start
    if 'densest' in task_list:
        start = timeit.default_timer()
        if args.heuristic == 'binomial-cuda':
            perm_entities, perm_relations = pre_randperm(full_graph.num_entities, full_graph.num_relations)
        densestSubgraph(args.dataset_name, args.embedding, heuristic, ratio, models)
        end = timeit.default_timer()
        tstamp_den = end - start
    if 'randomsample' in task_list:
        randomsample(args.embedding, args.dataset_name, nmb_KFold, size_subgraphs, models, entity_to_id_map, relation_to_id_map, all_triples_set, full_graph, ratio, heuristic)
    if 'approx' in task_list:
        path = f"approach/scoreData/time_measures_{args.embedding}_{args.dataset_name}_approx.csv"
        ex = os.path.isfile(path)
        c = open(f'{path}', "a+")
        writer = csv.writer(c)
        start = timeit.default_timer()
        densestSubgraph(args.dataset_name, args.embedding, getReliKScore, ratio, models)
        end = timeit.default_timer()
        data = ['accurate', ratio, end-start]
        writer.writerow(data)


        for rat in [0.05,0.1,0.15,0.2,0.25,0.3,0.35,0.4,0.45,0.5,0.55,0.6,0.65,0.7,0.75,0.8,0.85,0.9,0.95,1.0]:
            ratio = rat
            start = timeit.default_timer()
            densestSubgraph(args.dataset_name, args.embedding, lower_bound, ratio, models)
            end = timeit.default_timer()
            data = ['lower_bound', ratio, end-start]
            writer.writerow(data)

            start = timeit.default_timer()
            densestSubgraph(args.dataset_name, args.embedding, binomial, ratio, models)
            end = timeit.default_timer()
            data = ['binomial', ratio, end-start]
            writer.writerow(data)

        
        c.close()
        exit()

    
    path = f"approach/scoreData/time_measures.csv"
    ex = os.path.isfile(path)
    c = open(f'{path}', "a+")
    writer = csv.writer(c)
    if not ex:
        data = ['dataset','embedding','size subgraphs','nmb subgraphs','sib_time','prediction_time','triple_time','densest_time']
        writer.writerow(data)
    data = [args.dataset_name, args.embedding, size_subgraphs, n_subgraphs, tstamp_sib, tstamp_pre, tstamp_tpc, tstamp_den]
    writer.writerow(data)
    c.close()

