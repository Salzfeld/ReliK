DATASETNAME = 'CodexSmall'
EMBEDDING_VERSION = 2
EMBEDDING_TYPE = 'DistMult'
TESTNUMBER = 999
LP_EMB_SPLIT = 0.5
TRAIN_TEST_SPLIT = 0.33
RESET_PROB = 0.2
SIGFIGS = 5
AMOUNT_OF_SUBGRAPHS = 5
SIZE_OF_SUBGRAPHS = 5
NAME_OF_RUN = f"{DATASETNAME}_{TESTNUMBER}_{EMBEDDING_TYPE}"
SAVENAME = f"{DATASETNAME}_{EMBEDDING_VERSION}_{EMBEDDING_TYPE}"

STORE_MODEL = True
LOAD_MODEL = True
DOSCORE = True
DODIS = True
LOAD_TRIPLES = True
LOAD_SUBGRAPHS = True
MAKE_TRAINING_SMALLER = False
SMALLER_RATIO = 0.5

DO_THREE_BASED = False
DO_LABEL_BASED = False
DO_NOT_LABEL_BASED = True
ORIGINAL_LP = True

DO_NORM1 = False