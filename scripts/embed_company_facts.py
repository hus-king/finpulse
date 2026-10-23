"""Embed verbatim company cards using the same frozen Qwen model as the news."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from backend.attention_direction_data import company_cards


def embed(facts, model_path, output):
    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer
    bundle = company_cards(json.loads(facts.read_text()))
    if not bundle['cards']:
        raise ValueError('No source-backed company cards')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'company-cards.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n')
    digest = hashlib.sha256()
    with (model_path / 'model.safetensors').open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    tokenizer = AutoTokenizer.from_pretrained(model_path, padding_side='left', local_files_only=True)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = AutoModel.from_pretrained(model_path, torch_dtype=torch.float16 if device == 'cuda' else torch.float32,
                                      attn_implementation='sdpa', local_files_only=True).eval().to(device)
    start, vectors, truncated = time.monotonic(), [], 0
    for i in range(0, len(bundle['cards']), 2):
        cards = bundle['cards'][i:i + 2]
        texts = [f"公司代码：{c['code']}\n资料类型：{c['kind']}\n正文：{c['text']}" for c in cards]
        truncated += sum(len(tokenizer.encode(text)) > 1024 for text in texts)
        tokens = tokenizer(texts, padding=True, truncation=True, max_length=1024, return_tensors='pt').to(device)
        with torch.inference_mode():
            vector = torch.nn.functional.normalize(model(**tokens, use_cache=False).last_hidden_state[:, -1, :].float(), p=2, dim=1)
        vectors.append(vector.cpu().numpy())
    matrix = np.concatenate(vectors)
    if not np.isfinite(matrix).all():
        raise ValueError('Nonfinite company vectors')
    metadata = {'weights_sha256': digest.hexdigest(), 'dimension': matrix.shape[1], 'max_length': 1024,
                'pooling': 'left padding, last nonpadding token, L2 normalization',
                'input_format': 'company-code-card-kind-verbatim-text-v1; no retrieval instruction',
                'facts_sha256': hashlib.sha256(facts.read_bytes()).hexdigest(),
                'cards_sha256': hashlib.sha256((output / 'company-cards.json').read_bytes()).hexdigest(),
                'cards': len(bundle['cards']), 'versions': len(bundle['versions']),
                'truncated_cards': truncated, 'device': device, 'seconds': time.monotonic() - start}
    np.savez_compressed(output / 'company-embeddings.npz', ids=np.asarray([c['id'] for c in bundle['cards']]),
                        vectors=matrix, metadata=np.asarray(json.dumps(metadata)))
    print(json.dumps(metadata), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facts', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    embed(args.facts, args.model, args.output)
