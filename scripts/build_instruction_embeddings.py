"""Build HA-R2R embeddings in the dataset's exact word order from GloVe 6B."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

GLOVE_SHA256 = "d8f717f8dd4b545cb7f418ef9f3d0c3e6e68a6f48b97d32f8b7aae40cb31f96f"


def vocab_hash(words):
    return hashlib.sha256(json.dumps(words, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def build(dataset, glove, output):
    with gzip.open(dataset, "rt") as source:
        words = json.load(source)["instruction_vocab"]["word_list"]
    if words[:2] != ["<pad>", "<unk>"] or len(set(words)) != len(words):
        raise ValueError("Expected unique words with PAD=0 and UNK=1")
    digest = hashlib.sha256()
    vectors = {}
    wanted = set(words[2:])
    with Path(glove).open("rb") as source:
        for line in source:
            digest.update(line)
            word, _, values = line.decode().partition(" ")
            if word in wanted:
                vector = [float(value) for value in values.split()]
                if len(vector) != 50:
                    raise ValueError("Expected 50-dimensional GloVe vectors")
                vectors[word] = vector
    if digest.hexdigest() != GLOVE_SHA256:
        raise ValueError("Use the official glove.6B.50d.txt file")
    if not vectors:
        raise ValueError("No dataset words found in GloVe")
    # Use the mean of in-vocabulary GloVe vectors for UNK and OOV words.
    mean = [sum(vector[i] for vector in vectors.values()) / len(vectors) for i in range(50)]
    matrix = [[0.0] * 50, mean] + [vectors.get(word, mean) for word in words[2:]]
    payload = {
        "format_version": 1, "word_list": words, "vocab_sha256": vocab_hash(words),
        "vectors": matrix,
        "source": {"name": "GloVe 6B 50d", "url": "https://nlp.stanford.edu/data/glove.6B.zip",
                   "text_sha256": GLOVE_SHA256, "license": "PDDL-1.0"},
        "oov_words": [word for word in words[2:] if word not in vectors],
        "oov_policy": "mean of matched non-special dataset words",
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # No timestamp or filename: repeated builds produce identical bytes.
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as target:
        target.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    output.with_name("instruction_vocab.json").write_text(
        json.dumps({"word_list": words, "sha256": vocab_hash(words)}, ensure_ascii=False, indent=2) + "\n"
    )
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Ordinary HA-R2R train.json.gz, not *_bertidx")
    parser.add_argument("--glove", required=True, help="Official glove.6B.50d.txt")
    parser.add_argument("--output", default="Data/HA-R2R-tools/embeddings_glove_50d.json.gz")
    args = parser.parse_args()
    payload = build(args.dataset, args.glove, args.output)
    print("Built {} x 50; {} OOV words; vocabulary {}".format(
        len(payload["word_list"]), len(payload["oov_words"]), payload["vocab_sha256"]))


if __name__ == "__main__":
    main()
