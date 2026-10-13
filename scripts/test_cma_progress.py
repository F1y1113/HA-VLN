"""CPU regressions for stepwise progress supervision and padding isolation."""
import importlib.util
from pathlib import Path
import unittest

import torch

path = Path(__file__).resolve().parents[1] / 'agent/VLN-CE/vlnce_baselines/common/aux_losses.py'
spec = importlib.util.spec_from_file_location('cma_aux_losses', path)
aux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aux)


class ProgressTests(unittest.TestCase):
    def test_perfect_predictions_have_zero_loss_and_gradient(self):
        prediction = torch.tensor([[0.0], [0.5], [1.0]], requires_grad=True)
        loss = aux.progress_monitor_loss(prediction, prediction.detach().clone())
        self.assertEqual(loss.shape, (3,))
        self.assertTrue(torch.equal(loss, torch.zeros(3)))
        loss.mean().backward()
        self.assertTrue(torch.equal(prediction.grad, torch.zeros_like(prediction)))

    def test_flat_and_singleton_targets_agree(self):
        prediction = torch.tensor([[0.0], [0.5], [1.0]])
        target = torch.tensor([0.1, 0.4, 0.9])
        self.assertTrue(torch.equal(aux.progress_monitor_loss(prediction, target),
                                    aux.progress_monitor_loss(prediction, target[:, None])))

    def test_other_steps_and_padding_do_not_change_valid_gradient(self):
        gradients = []
        losses = []
        for padding_target in (1.0, -100.0):
            prediction = torch.tensor([[0.1], [0.3], [0.7]], requires_grad=True)
            target = torch.tensor([[0.0], [0.5], [padding_target]])
            collector = aux._AuxLosses()
            collector.activate()
            collector.register_loss('progress', aux.progress_monitor_loss(prediction, target))
            loss = collector.reduce(torch.tensor([True, True, False]))
            loss.backward()
            self.assertEqual(prediction.grad[-1].item(), 0.0)
            gradients.append(prediction.grad.clone())
            losses.append(loss.item())
        self.assertEqual(losses[0], losses[1])
        self.assertTrue(torch.equal(gradients[0], gradients[1]))
        torch.testing.assert_close(gradients[0], torch.tensor([[0.1], [-0.2], [0.0]]))

    def test_wrong_number_of_targets_is_rejected(self):
        with self.assertRaises(RuntimeError):
            aux.progress_monitor_loss(torch.zeros(3, 1), torch.zeros(3, 2))


if __name__ == '__main__':
    unittest.main()
