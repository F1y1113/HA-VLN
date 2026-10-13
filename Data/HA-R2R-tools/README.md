# HA-R2R instruction embeddings

The ordinary `train.json.gz`, `val_seen.json.gz`, and `val_unseen.json.gz`
share the same ordered 5,079-word vocabulary. CMA consumes their
`instruction_tokens` directly. The `*_bertidx.json.gz` files contain BERT token
IDs for the separate BERT-based model path.

`instruction_vocab.json` publishes the exact ordinary word order.
`embeddings_glove_50d.json.gz` contains that word list and a 5,079 x 50 matrix:
row `i` is the official GloVe 6B 50-dimensional vector for word `i`.
All 5,077 non-special words are present in GloVe. PAD (row 0) is zero; UNK
(row 1) is the mean of these 5,077 vectors. The initializer verifies word order,
dimensions, and the vocabulary fingerprint before use.

To rebuild it, obtain `glove.6B.zip` from the
[official GloVe project](https://nlp.stanford.edu/projects/glove/), extract
`glove.6B.50d.txt`, and run from the HA-VLN code repository root:

```bash
python scripts/build_instruction_embeddings.py \
  --dataset Data/HA-R2R/train/train.json.gz \
  --glove /path/to/glove.6B.50d.txt
```

The script verifies the source file's SHA-256 and creates deterministic output.
GloVe vectors are distributed under the PDDL 1.0 license.

## Released CMA checkpoint and transfer

The released HA-VLN-CMA `ckpt.39.pth` contains the 5,401 x 50 lookup used during
its training. This historical lookup does not follow the ordinary HA-R2R
vocabulary's GloVe word order. It remains part of the trained model: evaluation
restores its checkpoint embedding, rather than replacing it with the new
GloVe initializer. Its unused final 322 rows are omitted on load; every legal
HA-R2R token retains exactly the same vector. `embeddings.json.gz` is retained
as the historical lookup for earlier releases.

The loader binds this specific historical lookup to the audited HA-R2R
vocabulary by fingerprints. New training checkpoints include their ordered
vocabulary. An unlabelled checkpoint from another dataset is rejected in
`restore` mode, even when its matrix dimensions happen to match.

The original R2R CMA checkpoint's 2,504-word lookup belongs to the R2R
vocabulary. To initialize HA-R2R training from those other model weights,
explicitly select `MODEL.INSTRUCTION_ENCODER.checkpoint_embedding_mode transfer`
in the training configuration. Transfer keeps the new HA-R2R GloVe initializer
and loads all other weights; missing or unexpected non-embedding weights are
errors. Use the default `restore` mode to evaluate or resume a trained HA-R2R
checkpoint.
