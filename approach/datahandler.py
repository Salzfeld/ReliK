import random
from more_itertools import substrings
import networkx as nx
from numpy import typename
import pandas as pd
import ast
import numpy as np
import csv
import torch
import os

import settings as sett

from pykeen.triples import TriplesFactory
from sklearn.model_selection import KFold

def generateKFoldSplit(full_dataset, random_seed=None, n_split=5):
    kf = KFold(n_splits=n_split, random_state=random_seed, shuffle=True)
    fold_train_test_pairs = []
    isExist = os.path.exists(f"approach/KFold/{sett.DATASETNAME}_{n_split}_fold")

    if not isExist:
        os.makedirs(f"approach/KFold/{sett.DATASETNAME}_{n_split}_fold")

    for i, (train_index, test_index) in enumerate(kf.split(full_dataset)):
        c = open(f"approach/KFold/{sett.DATASETNAME}_{n_split}_fold/{i}_th_fold.csv", "w")
        writer = csv.writer(c)
        writer.writerows([train_index, test_index])
        c.close()
        fold_train_test_pairs.append([train_index.tolist(),test_index.tolist()])
    return fold_train_test_pairs


def loadKFoldSplit(ith_fold, n_split=5):
    with open(f"approach/KFold/{sett.DATASETNAME}_{n_split}_fold/{ith_fold}_th_fold.csv", "r") as f:
        rows = csv.reader(f, delimiter=',')
        i = 0
        train = []
        test = []
        for row in rows:
            if i==0:
                for ele in row:
                    train.append(int(ele))
            else:
                for ele in row:
                    test.append(int(ele))
            i += 1
    return train, test
    

def createNegTripleHT(kg_triple_set, kg_triple, triples):
    '''
    Creating negative triples
    By taking an existing triple ans swapping head and tail 
    so we get a non existing triple as neg triple
    '''
    kg_neg_triple_list = []
    related_nodes = set()
    lst_emb = list(range(triples.num_entities))
    bigcount = 0
    for pos_sample in kg_triple:
        related_nodes.add((pos_sample[0],pos_sample[2]))
        not_created = True
        relation = pos_sample[1]
        count = 0
        did_break = False
        while not_created:
            if count > (0.1*len(lst_emb)):
                did_break = True
                break
            head = random.choice(lst_emb)
            tail = random.choice(lst_emb)
            kg_neg_triple = [head,relation,tail]
            kg_neg_triple_tuple = (head,relation,tail)
            if (kg_neg_triple_tuple not in kg_triple_set):
                not_created = False
        if did_break:
            continue
        kg_neg_triple_list.append(kg_neg_triple)
        bigcount += 1
        if bigcount % 10000 == 0:
            print(f'Have created {bigcount} neg samples')

    return kg_neg_triple_list, related_nodes

def createNegTripleRelation(kg_triple_set, kg_triple, triples):
    '''
    Creating negative triples
    By taking an existing triple ans swapping head and tail 
    so we get a non existing triple as neg triple
    '''
    kg_neg_triple_list = []
    lst_emb = list(range(triples.num_relations))
    bigcount = 0
    for pos_sample in kg_triple:
        not_created = True
        head = pos_sample[0]
        tail = pos_sample[2]
        count = 0
        did_break = False
        while not_created:
            if count > (10 * len(lst_emb)):
                did_break = True
                break
            relation = random.choice(lst_emb)
            kg_neg_triple = [head,relation,tail]
            kg_neg_triple_tuple = (head,relation,tail)
            if (kg_neg_triple_tuple not in kg_triple_set):
                not_created = False
            count += 1
        if did_break:
            continue
        kg_neg_triple_list.append(kg_neg_triple)
        bigcount += 1
        if bigcount % 10000 == 0:
            print(f'Have created {bigcount} neg samples')

    return kg_neg_triple_list

def createSubGraphs(all_triples, entity_to_id, relation_to_id, number_of_graphs=10, size_of_graphs=20, restart=0.2):
    '''
    Creates subgraphs from the given KG by specific random walks with restart
    Returns all subgraphs in a list, each as a list of included nodes
    '''
    full_graph = TriplesFactory(all_triples,entity_to_id=entity_to_id,relation_to_id=relation_to_id)
    df = pd.DataFrame(full_graph.triples, columns=['subject', 'predicate', 'object'])
    G = nx.MultiDiGraph()

    for t in df.values:
        G.add_edge(t[0], t[2], label = t[1])
    subgraphs = []
    while len(subgraphs) < number_of_graphs:
        visited = set()
        node = random.choice(list(G.nodes()))
        original_node = node
        visited.add(node)
        all_neighbours = set()
        while len(visited) < size_of_graphs:
            if random.random() < restart:
                node = original_node
            else:
                neighbors = set(G.neighbors(node)) - visited
                all_neighbours = set.union(neighbors, all_neighbours) - visited
                if len(all_neighbours) == 0:
                    node = random.choice(list(G.nodes()))
                elif len(neighbors) == 0:
                    node = random.choice(list(all_neighbours))
                else:
                    node = random.choice(list(neighbors))
            visited.add(node)
        subgraphs.append(visited)
    return subgraphs

def storeSubGraphs(path, subgraphs):
    with open(f"{path}/subgraphs_{sett.SIZE_OF_SUBGRAPHS}.csv", "a+") as f:
        wr = csv.writer(f)
        wr.writerows(subgraphs)

def loadSubGraphs(path):
    with open(f"{path}/subgraphs_{sett.SIZE_OF_SUBGRAPHS}.csv", "r") as f:
        rows = csv.reader(f, delimiter=',')
        subgraphs = []
        for row in rows:
            subgraph = set()
            for ele in row:
                subgraph.add(ele)
            subgraphs.append(subgraph)
    return subgraphs

def storeTriples(path, triples):
    with open(f"{path}.csv", "a+") as f:
        wr = csv.writer(f)
        wr.writerows(triples)

def storeRelated(path, related):
    related = list(related)
    with open(f"{path}.csv", "w") as f:
        wr = csv.writer(f)
        wr.writerows(related)

def loadTriples(path):
    with open(f"{path}.csv", "r") as f:
        rows = csv.reader(f, delimiter=',')
        triples = []
        for row in rows:
            tp = [int(row[0]),int(row[1]),int(row[2])]
            triples.append(tp)
    return triples

def loadRelated(path):
    with open(f"{path}.csv", "r") as f:
        rows = csv.reader(f, delimiter=',')
        related_nodes = set()
        for row in rows:
            tp = (int(row[0]),int(row[1]))
            related_nodes.add(tp)
    return related_nodes


def convertListToData(sample_triples, triples, pos_sample=True):
    ds = []
    if pos_sample:
        for t in sample_triples:
            ds.append([triples.entity_id_to_label[t[0]], triples.relation_id_to_label[t[1]], triples.entity_id_to_label[t[2]], 1])
    else:
        for t in sample_triples:
            ds.append([triples.entity_id_to_label[t[0]], triples.relation_id_to_label[t[1]], triples.entity_id_to_label[t[2]], 0])

    dataset = np.array(ds)

    X = dataset[:, :-1]
    y = dataset[:, -1]

    return X, y

def convertListToData_Relation(sample_triples, triples, pos_sample=True):
    ds = dict()
    for i in range(triples.num_relations):
        ds[i] = []
    if pos_sample:
        for t in sample_triples:
            ds[t[1]].append([triples.entity_id_to_label[t[0]], triples.relation_id_to_label[t[1]], triples.entity_id_to_label[t[2]], 1])
    else:
        for t in sample_triples:
            ds[t[1]].append([triples.entity_id_to_label[t[0]], triples.relation_id_to_label[t[1]], triples.entity_id_to_label[t[2]], 0])

    X_dict = dict()
    y_dict = dict()
    for i in range(triples.num_relations):
        dataset = np.array(ds[i])
        X = dataset[:, :-1]
        y = dataset[:, -1]
        X_dict[i] = X
        y_dict[i] = y

    return X_dict, y_dict
