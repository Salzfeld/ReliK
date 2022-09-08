import torch

import pykeen.datasets as dat

from pykeen.models import TransE
from pykeen.models import DistMult
from pykeen.pipeline import pipeline

def getDataFromPykeen(datasetname='Nations'):
    '''
    Using datasets from the pykeen library, and preparing data for our implementaton
    '''
    if datasetname == 'Nations':
        dataset = dat.Nations()
    elif datasetname == 'Countries':
        dataset = dat.Countries()
    elif datasetname == 'Kinships':
        dataset = dat.Kinships()
    elif datasetname == 'UML':
        dataset = dat.UMLS()
    elif datasetname == 'YAGO3-10':
        dataset = dat.YAGO310()
    elif datasetname == 'Hetionet':
         dataset = dat.Hetionet()
    elif datasetname == 'FB15k':
        dataset = dat.FB15k()
    elif datasetname == 'DBpedia50':
        dataset = dat.DBpedia50()
    elif datasetname == 'CodexSmall':
        dataset = dat.CoDExSmall()
    elif datasetname == 'CodexMedium':
        dataset = dat.CoDExMedium()
    elif datasetname == 'CodexLarge':
        dataset = dat.CoDExLarge()
    elif datasetname == 'FB15k237':
        dataset = dat.FB15k237()

    entity_to_id_map = dataset.entity_to_id
    relation_to_id_map = dataset.relation_to_id
    #all_triples_tensor = torch.cat((dataset.training.mapped_triples,dataset.validation.mapped_triples,dataset.testing.mapped_triples))
    all_triples_tensor = dataset.training.mapped_triples
    all_triples_set = set()
    for tup in all_triples_tensor.tolist():
        all_triples_set.add((tup[0],tup[1],tup[2]))

    return all_triples_tensor, all_triples_set, entity_to_id_map, relation_to_id_map

def trainEmbedding(training_set, test_set, random_seed=None, saveModel = False, savename="Test"):
    '''
    Train embedding for given triples
    '''
    if random_seed == None:
        result = pipeline(training=training_set,testing=test_set,model=TransE,training_loop='LCWA')
    else:
        result = pipeline(training=training_set,testing=test_set,model=TransE,random_seed=random_seed,training_loop='LCWA')

    if saveModel:
        result.save_to_directory(f"approach/trainedEmbeddings/{savename}")

    return result.model, result.training

def loadModel(savename="Test"):
    model = torch.load(f"approach/trainedEmbeddings/{savename}/trained_model.pkl")
    return model

def createEmbeddingMaps(model, triples):
    '''
    create maps of the embedding to the respective entities and relations, for easier reuse
    '''
    e_emb = model.entity_embeddings.cpu()
    entity_ids = torch.LongTensor(range(triples.num_entities))
    e_emb_numpy = e_emb(entity_ids).detach().numpy()
    entity2embedding = {}
    for eid in range(triples.num_entities):
        e = triples.entity_id_to_label[eid]
        entity2embedding[e] = list(e_emb_numpy[eid])
        # Sanity Check if conversion stays consistent from id to labels
        assert triples.entity_to_id[e] == eid, 'Entity IDs not consistent'

    r_emb = model.relation_embeddings.cpu()
    relation_ids = torch.LongTensor(range(triples.num_relations))
    r_emb_numpy = r_emb(relation_ids).detach().numpy()
    relation2embedding = {}
    for rid in range(triples.num_relations):
        r = triples.relation_id_to_label[rid]
        relation2embedding[r] = list(r_emb_numpy[rid])
        # Sanity Check if conversion stays consistent from id to labels
        assert triples.relation_to_id[r] == rid, 'Relation IDs not consistent'

    return entity2embedding, relation2embedding

def getScoreForTripleListSubgraphs(X_test, emb_train_triples, model, subgraphs):
    score_list = []
    for subgraph in subgraphs:
        sum = 0
        had_element = False
        for tp in X_test:
            if (tp[0] in subgraph) and (tp[2] in subgraph):
                ten = torch.tensor([[emb_train_triples.entity_to_id[tp[0]],emb_train_triples.relation_to_id[tp[1]],emb_train_triples.entity_to_id[tp[2]]]])
                score = model.score_hrt(ten)
                score = score.detach().numpy()[0][0]
                sum += score
                had_element = True
        if had_element:
            score_list.append(sum)
        else:
            score_list.append(-1)
    return score_list

def getScoreForTripleList(X_test, emb_train_triples, model):
    score_list = []
    for tp in X_test:
        ten = torch.tensor([[emb_train_triples.entity_to_id[tp[0]],emb_train_triples.relation_to_id[tp[1]],emb_train_triples.entity_to_id[tp[2]]]])
        score = model.score_hrt(ten)
        score = score.detach().numpy()[0][0]
        score_list.append(score)
    return score_list