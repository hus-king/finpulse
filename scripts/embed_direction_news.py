"""Cache frozen, normalized Qwen3 document vectors for the offline experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from backend.news_direction import documents


def embedding_text(article):
    return f"公司：{article.get('company', '')}（{article['code']}）\n标题：{article['title']}\n正文：{article['text']}"


def text_chunks(tokenizer, article, max_length):
    """Repeat source company/title context; cover every body token with overlap."""
    prefix = tokenizer.encode(embedding_text({**article, 'text': ''}), add_special_tokens=False)
    body = tokenizer.encode(article['text'], add_special_tokens=False)
    special = tokenizer.num_special_tokens_to_add(pair=False)
    # build_inputs_with_special_tokens was removed from Qwen2Tokenizer in
    # transformers 5. Qwen3-Embedding has no BOS and a single trailing end
    # token; append exactly what encode('', add_special_tokens=True) adds so
    # the chunked path matches the non-chunked path's inputs.
    end_ids = tokenizer.encode('', add_special_tokens=True) or []
    if len(prefix) >= max_length - special - 64:
        raise ValueError('Company/title prefix leaves no safe body chunk budget')
    width = max_length - special - len(prefix)
    overlap = min(64, width // 4)
    chunks = []
    offset = 0
    while offset < len(body) or not chunks:
        piece = body[offset:offset + width]
        ids = prefix + piece + end_ids
        # Count repeated overlap once in the final document average.
        weight = max(1, len(piece) - (overlap if offset else 0))
        chunks.append((ids, weight))
        if offset + width >= len(body):
            break
        offset += width - overlap
    return chunks


def embed(corpus_path, model_path, output, max_length=1024, chunk_long_texts=False):
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
    vectors, truncated, chunk_count, chunked_documents = [], 0, 0, 0
    if chunk_long_texts:
        for index, article in enumerate(articles):
            chunks = text_chunks(tokenizer, article, max_length)
            chunk_count += len(chunks)
            chunked_documents += len(chunks) > 1
            total = None
            for offset in range(0, len(chunks), 2):
                part = chunks[offset:offset + 2]
                tokens = tokenizer.pad([{'input_ids': ids, 'attention_mask': [1] * len(ids)} for ids, _ in part],
                                       padding=True, return_tensors='pt').to(device)
                with torch.inference_mode():
                    hidden = model(**tokens, use_cache=False).last_hidden_state[:, -1, :]
                    vector = torch.nn.functional.normalize(hidden.float(), p=2, dim=1)
                    weighted = (vector * torch.tensor([weight for _, weight in part], device=device)[:, None]).sum(0)
                    total = weighted if total is None else total + weighted
            vector = torch.nn.functional.normalize(total, p=2, dim=0)
            if not torch.isfinite(vector).all():
                raise ValueError('Non-finite chunked embedding')
            vectors.append(vector.cpu().numpy()[None])
            if index % 20 == 0:
                print(json.dumps({'embedded': index + 1, 'total': len(articles), 'chunks': chunk_count}), flush=True)
    else:
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
    if chunk_long_texts:
        metadata.update(input_format='company-code-title-body-chunks-v2; no retrieval instruction',
                        long_text_strategy='repeat company/title; overlap up to 64; new-token-weighted mean; L2',
                        chunked_documents=chunked_documents, chunks=chunk_count)
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
    parser.add_argument('--chunk-long-texts', action='store_true', help='Encode all body chunks instead of truncating the tail')
    args = parser.parse_args()
    if not 128 <= args.max_length <= 2048:
        parser.error('--max-length must be between 128 and 2048 for the 4GB deployment')
    embed(args.corpus, args.model, args.output, args.max_length, args.chunk_long_texts)
