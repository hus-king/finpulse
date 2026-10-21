"""Cache frozen, normalized Qwen3 document vectors for the offline experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from backend.news_direction import documents


def embedding_text(article):
    return f"公司：{article.get('company', '')}（{article['code']}）\n标题：{article['title']}\n正文：{article['text']}"


def embed(corpus_path, model_path, output, max_length=1024):
    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer
    corpus_bytes = corpus_path.read_bytes()
    corpus = json.loads(corpus_bytes)
    articles, _ = documents(corpus)
    articles.sort(key=lambda a: a['id'])
    if not articles:
        raise ValueError('No full-body, date-verified news to embed')
    start = time.monotonic()
    weights = model_path / 'model.safetensors'
    digest = hashlib.sha256()
    with weights.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    weight_hash = digest.hexdigest()
    tokenizer = AutoTokenizer.from_pretrained(model_path, padding_side='left', local_files_only=True)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    torch.set_num_threads(4)
    model = AutoModel.from_pretrained(model_path, torch_dtype=dtype, attn_implementation='sdpa',
                                     local_files_only=True).eval().to(device)
    vectors, truncated = [], 0
    for index in range(0, len(articles), 2):
        texts = [embedding_text(a) for a in articles[index:index + 2]]
        lengths = [len(tokenizer.encode(text)) for text in texts]
        truncated += sum(length > max_length for length in lengths)
        tokens = tokenizer(texts, padding=True, truncation=True, max_length=max_length,
                           return_tensors='pt').to(device)
        with torch.inference_mode():
            hidden = model(**tokens, use_cache=False).last_hidden_state[:, -1, :]
            vector = torch.nn.functional.normalize(hidden.float(), p=2, dim=1)
        if not torch.isfinite(vector).all():
            raise ValueError('Non-finite embedding')
        vectors.append(vector.cpu().numpy())
        if index % 20 == 0:
            print(json.dumps({'embedded': min(index + 2, len(articles)), 'total': len(articles)}), flush=True)
    metadata = {'schema_version': 1, 'model': 'Qwen3-Embedding-0.6B', 'weights_sha256': weight_hash,
                'corpus_sha256': hashlib.sha256(corpus_bytes).hexdigest(), 'max_length': max_length,
                'input_format': 'company-code-title-body-v1; no retrieval instruction',
                'pooling': 'left padding, last nonpadding token, L2 normalization',
                'dimension': vectors[0].shape[1], 'documents': len(articles),
                'truncated_documents': truncated, 'device': device, 'seconds': time.monotonic() - start}
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, ids=np.asarray([a['id'] for a in articles]), vectors=np.concatenate(vectors),
                        metadata=np.asarray(json.dumps(metadata)))
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-length', type=int, default=1024)
    args = parser.parse_args()
    if not 128 <= args.max_length <= 2048:
        parser.error('--max-length must be between 128 and 2048 for the 4GB deployment')
    embed(args.corpus, args.model, args.output, args.max_length)
