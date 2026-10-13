"""Word-order validation and explicit checkpoint embedding restoration."""
import gzip
import hashlib
import json

import torch

EMBEDDING_KEY = "net.instruction_encoder.embedding_layer.weight"
# Bind the released HA-VLN-CMA training lookup to its actual HA-R2R vocabulary.
# These fingerprints are populated from the audited release, not from row counts.
LEGACY_VOCAB_SHA256 = "37b0c74d3ef29e5efa1c93dac09add0e63bc2b47f8b4f22bc41d1e28e3c66db2"
LEGACY_EMBEDDING_SHA256 = "229d82eb667a73a015b988e1b3e303d57669dd7e22112c913478788fe5096f21"


def vocab_hash(words):
    return hashlib.sha256(json.dumps(words, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def read_word_list(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as source:
        data = json.load(source)
    words = data.get("instruction_vocab", data)["word_list"]
    if words[:2] != ["<pad>", "<unk>"] or len(set(words)) != len(words):
        raise ValueError("Expected unique instruction words with PAD=0 and UNK=1")
    return words


def load_embeddings(path, words, dimension):
    with gzip.open(path, "rt") as source:
        data = json.load(source)
    if not isinstance(data, dict) or data.get("word_list") != words:
        raise ValueError("Embedding word order must match the dataset vocabulary; use embeddings_glove_50d.json.gz")
    if data.get("vocab_sha256") != vocab_hash(words):
        raise ValueError("Embedding vocabulary fingerprint mismatch")
    vectors = torch.tensor(data["vectors"], dtype=torch.float32)
    if vectors.shape != (len(words), dimension) or not torch.isfinite(vectors).all():
        raise ValueError("Invalid embedding shape or non-finite vectors")
    if torch.count_nonzero(vectors[0]):
        raise ValueError("PAD embedding must be zero")
    return vectors


def checkpoint_vocab(words):
    return {"word_list": words, "sha256": vocab_hash(words)}


def load_policy_checkpoint(policy, checkpoint, words, mode="restore"):
    """Restore a model's own lookup, or explicitly transfer all other weights."""
    state = dict(checkpoint["state_dict"])
    if mode not in ("restore", "transfer"):
        raise ValueError("checkpoint_embedding_mode must be restore or transfer")
    if EMBEDDING_KEY not in policy.state_dict():
        policy.load_state_dict(state, strict=True)
        return
    if EMBEDDING_KEY not in state:
        raise ValueError("Checkpoint is missing its instruction embedding")
    if mode == "transfer":
        # Preserve the new dataset's aligned initializer. Only this key may be absent.
        del state[EMBEDDING_KEY]
        result = policy.load_state_dict(state, strict=False)
        if result.missing_keys != [EMBEDDING_KEY] or result.unexpected_keys:
            raise ValueError("Transfer checkpoint has missing or unexpected model weights")
        return
    vectors = state[EMBEDDING_KEY].detach().cpu().float().contiguous()
    metadata = checkpoint.get("instruction_vocab")
    if metadata is not None:
        if metadata != checkpoint_vocab(words):
            raise ValueError("Checkpoint instruction vocabulary does not match this dataset")
        if vectors.shape[0] != len(words):
            raise ValueError("Checkpoint embedding row count does not match its vocabulary")
    else:
        fingerprint = hashlib.sha256(vectors.numpy().tobytes()).hexdigest()
        if vocab_hash(words) != LEGACY_VOCAB_SHA256 or fingerprint != LEGACY_EMBEDDING_SHA256:
            raise ValueError("Checkpoint lacks a matching instruction vocabulary. Use explicit transfer for a different dataset")
        # Rows 5079..5400 were unused throughout HA-R2R training and validation.
        vectors = vectors[:len(words)]
    if vectors.shape != policy.state_dict()[EMBEDDING_KEY].shape or not torch.isfinite(vectors).all():
        raise ValueError("Checkpoint instruction embedding shape or values are invalid")
    if torch.count_nonzero(vectors[0]):
        raise ValueError("Checkpoint PAD embedding must be zero")
    state[EMBEDDING_KEY] = vectors
    policy.load_state_dict(state, strict=True)
