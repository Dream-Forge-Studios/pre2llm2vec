import datasets
import torch
from llm2vec import LLM2Vec
from beir import util
from beir.datasets.data_loader import GenericDataLoader as BeirDataLoader
import os 
from typing import Dict, List

from beir.retrieval.evaluation import EvaluateRetrieval

import random
from datasets import load_dataset, DatasetDict
from transformers import AutoModel, AutoTokenizer

instruction = "Given a question query, retrieve relevant documents that answer the query."

print("Loading dataset...")
# 시드 설정
random.seed(42)
file_path = "maywell/ko_wikidata_QA"
cache_dir = "/data/llm/"
cache_dir = "D:\\huggingface\\cache"

raw_datasets = load_dataset(file_path, cache_dir=cache_dir)

# train_testsplit = raw_datasets['train'].train_test_split(test_size=0.1, seed=42)
train_testsplit = raw_datasets['train'].train_test_split(test_size=0.001, seed=42)
fix_datasets = DatasetDict({
    'train': train_testsplit['train'],
    'validation': train_testsplit['test']
})
corpus = {}
queries = {}
relevant_docs = {}
id = 1
for dataset in fix_datasets['validation']:
    corpus['output' + str(id)] = {}
    corpus['output' + str(id)]['text'] = dataset['output']
    queries['instruction' + str(id)] = dataset['instruction']
    relevant_docs['instruction' + str(id)] = {}
    relevant_docs['instruction' + str(id)]['output' + str(id)] = 1

    id += 1
batch_size = 8

print("Loading model...")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

model = AutoModel.from_pretrained('BM-K/KoSimCSE-roberta', cache_dir="D:\\huggingface\\cache").to(device)
tokenizer = AutoTokenizer.from_pretrained('BM-K/KoSimCSE-roberta', cache_dir="D:\\huggingface\\cache")

def append_instruction(instruction, sentences):
    new_sentences = []
    for s in sentences:
        new_sentences.append([instruction, s, 0])
    return new_sentences

def cos_sim(a: torch.Tensor, b: torch.Tensor):
    if not isinstance(a, torch.Tensor):
        a = torch.tensor(a)

    if not isinstance(b, torch.Tensor):
        b = torch.tensor(b)

    if len(a.shape) == 1:
        a = a.unsqueeze(0)

    if len(b.shape) == 1:
        b = b.unsqueeze(0)

    a_norm = torch.nn.functional.normalize(a, p=2, dim=1)
    b_norm = torch.nn.functional.normalize(b, p=2, dim=1)
    return torch.mm(a_norm, b_norm.transpose(0, 1))

def get_embeddings(model, tokenizer, sentences, device):
    inputs = tokenizer(sentences, padding=True, truncation=True, return_tensors="pt", max_length=128).to(device)
    with torch.no_grad():  # 그라디언트 계산 비활성화
        outputs = model(**inputs)
        embeddings = outputs.last_hidden_state[:, 0, :]  # 첫번째 토큰(보통 CLS 토큰) 사용
    return embeddings

def encode_corpus(corpus: List[Dict[str, str]], model, tokenizer, device):
    if type(corpus) is dict:
        sentences = [
            (corpus["title"][i] + ' ' + corpus["text"][i]).strip()
            if "title" in corpus
            else corpus["text"][i].strip()
            for i in range(len(corpus["text"]))
        ]
    else:
        sentences = [
            (doc["title"] + ' ' + doc["text"]).strip() if "title" in doc else doc["text"].strip()
            for doc in corpus
        ]
    return get_embeddings(model, tokenizer, sentences, device)


print("Encoding Queries...")
query_ids = list(queries.keys())
results = {qid: {} for qid in query_ids}
queries = [queries[qid] for qid in queries]
query_embeddings = get_embeddings(model, tokenizer, queries, device)

print("Sorting Corpus by document length (Longest first)...")
corpus_ids = sorted(corpus, key=lambda k: len(corpus[k].get("title", "") + corpus[k].get("text", "")), reverse=True)
corpus = [corpus[cid] for cid in corpus_ids]

print("Encoding Corpus ... Warning: This might take a while!")
corpus_embeddings = encode_corpus(
    corpus,
    model=model,
    tokenizer=tokenizer,
    device = device
    )

print("Scoring Function: {} ({})".format("Cosine Similarity", "cos_sim"))
cos_scores = cos_sim(query_embeddings, corpus_embeddings)
cos_scores[torch.isnan(cos_scores)] = -1

#Get top-k values
top_k = 1000
cos_scores_top_k_values, cos_scores_top_k_idx = torch.topk(cos_scores, min(top_k+1, len(cos_scores[0])), dim=1, largest=True, sorted=False)
cos_scores_top_k_values = cos_scores_top_k_values.cpu().tolist()
cos_scores_top_k_idx = cos_scores_top_k_idx.cpu().tolist()

for query_itr in range(len(query_embeddings)):
    query_id = query_ids[query_itr]                  
    for sub_corpus_id, score in zip(cos_scores_top_k_idx[query_itr], cos_scores_top_k_values[query_itr]):
        corpus_id = corpus_ids[sub_corpus_id]
        if corpus_id != query_id:
            results[query_id][corpus_id] = score

retriever = EvaluateRetrieval(model, score_function="cos_sim")
ndcg, _map, recall, precision = retriever.evaluate(relevant_docs, results, retriever.k_values)
mrr = retriever.evaluate_custom(relevant_docs, results, retriever.k_values, "mrr")

scores = {
    **{f"ndcg_at_{k.split('@')[1]}": v for (k, v) in ndcg.items()},
    **{f"map_at_{k.split('@')[1]}": v for (k, v) in _map.items()},
    **{f"recall_at_{k.split('@')[1]}": v for (k, v) in recall.items()},
    **{f"precision_at_{k.split('@')[1]}": v for (k, v) in precision.items()},
    **{f"mrr_at_{k.split('@')[1]}": v for (k, v) in mrr.items()},
}
print(scores)
"""
{
    'ndcg_at_1': 0.32788, 
    'ndcg_at_3': 0.47534, 
    'ndcg_at_5': 0.52296, 
    'ndcg_at_10': 0.57505, 
    'ndcg_at_100': 0.6076,
    'ndcg_at_1000': 0.60801,
	'map_at_1': 0.32788,
	'map_at_3': 0.43883,
	'map_at_5': 0.46518,
	'map_at_10': 0.48675,
	'map_at_100': 0.49506,
	'map_at_1000': 0.49509,
	'recall_at_1': 0.32788,
	'recall_at_3': 0.58108,
	'recall_at_5': 0.69701,
	'recall_at_10': 0.85775,
	'recall_at_100': 0.9936,
	'recall_at_1000': 0.99644,
	'precision_at_1': 0.32788,
	'precision_at_3': 0.19369,
	'precision_at_5': 0.1394,
	'precision_at_10': 0.08578,
	'precision_at_100': 0.00994,
	'precision_at_1000': 0.001,
	'mrr_at_1': 0.33357,
	'mrr_at_3': 0.44085,
	'mrr_at_5': 0.46745,
	'mrr_at_10': 0.4888,
	'mrr_at_100': 0.49718,
	'mrr_at_1000': 0.49721}
"""