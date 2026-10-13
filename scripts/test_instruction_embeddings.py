"""Regression tests for vocabulary binding and checkpoint restoration (CPU)."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]


def import_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


embeddings = import_file("instruction_embeddings", ROOT / "agent/VLN-CE/vlnce_baselines/common/instruction_embeddings.py")
builder = import_file("build_instruction_embeddings", ROOT / "scripts/build_instruction_embeddings.py")


def policy(rows=4):
    model = nn.Module()
    model.net = nn.Module()
    model.net.instruction_encoder = nn.Module()
    model.net.instruction_encoder.embedding_layer = nn.Embedding(rows, 2, padding_idx=0)
    model.output = nn.Linear(2, 1)
    return model


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.words = ["<pad>", "<unk>", "left", "right"]

    def test_builder_matches_words_not_source_line_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "train.json.gz"
            with gzip.open(dataset, "wt") as source:
                json.dump({"instruction_vocab": {"word_list": self.words}}, source)
            glove = root / "glove.txt"
            glove.write_text("right " + " ".join(["2"] * 50) + "\nleft " + " ".join(["1"] * 50) + "\n")
            target = root / "embeddings.json.gz"
            with patch.object(builder, "GLOVE_SHA256", hashlib.sha256(glove.read_bytes()).hexdigest()):
                data = builder.build(dataset, glove, target)
                first = target.read_bytes()
                builder.build(dataset, glove, target)
            self.assertEqual(target.read_bytes(), first)
            self.assertEqual(data["vectors"][0], [0] * 50)
            self.assertEqual(data["vectors"][1], [1.5] * 50)
            self.assertEqual(data["vectors"][2], [1] * 50)
            self.assertEqual(data["vectors"][3], [2] * 50)
            self.assertEqual(embeddings.read_word_list(root / "instruction_vocab.json"), self.words)

    def test_same_size_permuted_vocabulary_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vectors.gz"
            with gzip.open(path, "wt") as source:
                json.dump({"word_list": self.words, "vocab_sha256": embeddings.vocab_hash(self.words), "vectors": [[0, 0]] * 4}, source)
            with self.assertRaisesRegex(ValueError, "word order"):
                embeddings.load_embeddings(path, self.words[:2] + ["right", "left"], 2)

    def test_legacy_unlabelled_matrix_is_not_a_training_initializer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vectors.gz"
            with gzip.open(path, "wt") as source:
                json.dump([[0, 0]] * 4, source)
            with self.assertRaisesRegex(ValueError, "word order"):
                embeddings.load_embeddings(path, self.words, 2)

    def test_restore_uses_checkpoint_embedding(self):
        original, restored = policy(), policy()
        checkpoint = {"state_dict": original.state_dict(), "instruction_vocab": embeddings.checkpoint_vocab(self.words)}
        embeddings.load_policy_checkpoint(restored, checkpoint, self.words)
        for key, tensor in original.state_dict().items():
            self.assertTrue(torch.equal(tensor, restored.state_dict()[key]))

    def test_checkpoint_permuted_vocabulary_is_rejected(self):
        checkpoint = {"state_dict": policy().state_dict(), "instruction_vocab": embeddings.checkpoint_vocab(self.words[::-1])}
        with self.assertRaisesRegex(ValueError, "vocabulary"):
            embeddings.load_policy_checkpoint(policy(), checkpoint, self.words)

    def test_unidentified_checkpoint_is_rejected_even_if_shape_matches(self):
        with self.assertRaisesRegex(ValueError, "lacks a matching"):
            embeddings.load_policy_checkpoint(policy(), {"state_dict": policy().state_dict()}, self.words)

    def test_transfer_preserves_initializer_and_loads_other_weights(self):
        source, target = policy(3), policy(4)
        before = target.net.instruction_encoder.embedding_layer.weight.detach().clone()
        embeddings.load_policy_checkpoint(target, {"state_dict": source.state_dict()}, self.words, "transfer")
        self.assertTrue(torch.equal(before, target.net.instruction_encoder.embedding_layer.weight))
        self.assertTrue(torch.equal(source.output.weight, target.output.weight))

    def test_transfer_rejects_missing_non_embedding_weight(self):
        state = dict(policy().state_dict())
        del state["output.weight"]
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            embeddings.load_policy_checkpoint(policy(), {"state_dict": state}, self.words, "transfer")

    def test_restore_rejects_missing_non_embedding_weight(self):
        state = dict(policy().state_dict())
        del state["output.weight"]
        with self.assertRaises(RuntimeError):
            embeddings.load_policy_checkpoint(policy(), {"state_dict": state, "instruction_vocab": embeddings.checkpoint_vocab(self.words)}, self.words)

    def test_restore_requires_checkpoint_embedding(self):
        state = dict(policy().state_dict())
        del state[embeddings.EMBEDDING_KEY]
        with self.assertRaisesRegex(ValueError, "missing its instruction"):
            embeddings.load_policy_checkpoint(policy(), {"state_dict": state}, self.words)


if __name__ == "__main__":
    unittest.main()
