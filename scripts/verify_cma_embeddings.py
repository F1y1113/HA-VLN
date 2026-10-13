"""Verify published CMA lookup compatibility with all HA-R2R instructions.

Run in the official CMA environment. This uses the actual instruction encoder
and full policy with synthetic visual inputs; it does not run scene replay.
"""
import argparse
import gzip
import json
from pathlib import Path
import sys
import types

import numpy as np
import torch
from gym import spaces

ROOT = Path(__file__).resolve().parents[1]
# Import the actual model without importing optional trainer dependencies.
package = types.ModuleType("vlnce_baselines")
package.__path__ = [str(ROOT / "agent/VLN-CE/vlnce_baselines")]
sys.modules["vlnce_baselines"] = package
from vlnce_baselines.config.default import get_config
from vlnce_baselines.common.instruction_embeddings import load_policy_checkpoint, read_word_list
from vlnce_baselines.models.cma_policy import CMAPolicy


def verify(checkpoint_path, dataset_dir, device):
    torch.set_num_threads(4)
    torch.manual_seed(30)
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    config = get_config(str(ROOT / "agent/config/cma_pm_da_aug_tune.yaml"))
    config.defrost()
    config.MODEL.DEPTH_ENCODER.ddppo_checkpoint = "NONE"
    config.MODEL.INSTRUCTION_ENCODER.embedding_file = str(ROOT / "Data/HA-R2R-tools/embeddings_glove_50d.json.gz")
    config.MODEL.INSTRUCTION_ENCODER.dataset_vocab = str(ROOT / "Data/HA-R2R-tools/instruction_vocab.json")
    config.freeze()
    observation_space = spaces.Dict({
        "rgb": spaces.Box(0, 255, (224, 224, 3), dtype=np.uint8),
        "depth": spaces.Box(0, 1, (256, 256, 1), dtype=np.float32),
        "instruction": spaces.Box(0, 5078, (200,), dtype=np.int64),
    })
    old = CMAPolicy.from_config(config, observation_space, spaces.Discrete(4))
    new = CMAPolicy.from_config(config, observation_space, spaces.Discrete(4))
    key = "net.instruction_encoder.embedding_layer.weight"
    old.net.instruction_encoder.embedding_layer = torch.nn.Embedding.from_pretrained(checkpoint["state_dict"][key], freeze=True)
    old.load_state_dict(checkpoint["state_dict"], strict=True)
    words = read_word_list(ROOT / "Data/HA-R2R-tools/instruction_vocab.json")
    load_policy_checkpoint(new, checkpoint, words)
    old, new = old.to(device).eval(), new.to(device).eval()
    report = {"device": device, "torch": torch.__version__, "splits": {}, "policy_steps": 0}
    examples = []
    with torch.no_grad():
        for split in ("train", "val_seen", "val_unseen"):
            with gzip.open(Path(dataset_dir) / split / (split + ".json.gz"), "rt") as source:
                data = json.load(source)
            assert data["instruction_vocab"]["word_list"] == words, split
            tokens = [episode["instruction"]["instruction_tokens"] for episode in data["episodes"]]
            max_id = max(max(row) for row in tokens)
            assert max_id < len(words)
            for start in range(0, len(tokens), 64):
                rows = tokens[start:start + 64]
                batch = torch.zeros(len(rows), max(len(row) for row in rows), dtype=torch.long, device=device)
                for index, row in enumerate(rows):
                    batch[index, :len(row)] = torch.tensor(row, device=device)
                assert torch.equal(old.net.instruction_encoder({"instruction": batch}), new.net.instruction_encoder({"instruction": batch})), (split, start)
            report["splits"][split] = {"episodes": len(tokens), "max_token_id": max_id, "encoder_outputs_bitwise_equal": True}
            examples.extend(tokens[:2])
        instruction = torch.tensor(examples, device=device)
        observations = {"instruction": instruction,
                        "rgb": torch.randint(0, 256, (len(examples), 224, 224, 3), dtype=torch.uint8, device=device),
                        "depth": torch.rand(len(examples), 256, 256, 1, device=device)}
        state_old = torch.zeros(len(examples), old.net.num_recurrent_layers, config.MODEL.STATE_ENCODER.hidden_size, device=device)
        state_new = state_old.clone()
        previous = torch.zeros(len(examples), 1, dtype=torch.long, device=device)
        masks = torch.zeros(len(examples), 1, dtype=torch.uint8, device=device)
        for step in range(10):
            features_old, state_old = old.net(observations, state_old, previous, masks)
            features_new, state_new = new.net(observations, state_new, previous, masks)
            logits_old = old.action_distribution(features_old).logits
            logits_new = new.action_distribution(features_new).logits
            assert torch.equal(features_old, features_new) and torch.equal(state_old, state_new)
            assert torch.equal(logits_old, logits_new)
            previous = logits_old.argmax(dim=-1, keepdim=True)
            masks.fill_(1)
            report["policy_steps"] += len(examples)
    report["policy_features_states_logits_bitwise_equal"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--dataset-dir", required=True, help="HA-R2R directory with ordinary train/val_seen/val_unseen splits")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = verify(args.checkpoint, args.dataset_dir, args.device)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
