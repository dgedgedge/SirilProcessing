"""Réseau de restauration Cosmic Clarity, adapté du code MIT de Franklin Marek.

Architecture et noms des couches compatibles avec le commit amont
423acc442724872ff6d81011962a8cbf5744a7f7. Licence conservée dans LICENSE.
Ce module optionnel importe PyTorch ; l'orchestrateur ne l'importe pas au repos.
"""

from __future__ import annotations

import torch
from torch import nn


class ResidualBlock(nn.Module):
    """Deux convolutions 3 × 3 avec connexion résiduelle, dimensions conservées."""

    def __init__(self, channels: int) -> None:
        """Construit le bloc avec le nombre de canaux du checkpoint."""
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=1)
        self.relu = nn.ReLU()
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Transforme un tenseur NCHW sans modifier sa forme."""
        return self.relu(self.conv2(self.relu(self.conv1(x))) + x)


class RestorationCNN(nn.Module):
    """Réseau à connexions latérales, sortie RGB normalisée par une sigmoïde."""

    def __init__(self) -> None:
        """Instancie les couches portant les noms exacts des poids amont."""
        super().__init__()
        inputs = (3, 16, 32, 64, 128)
        outputs = (16, 32, 64, 128, 256)
        for index, (inc, outc) in enumerate(zip(inputs, outputs), start=1):
            dilation = 2 if index in (3, 5) else 1
            setattr(self, f'encoder{index}', nn.Sequential(
                nn.Conv2d(inc, outc, 3, padding=dilation, dilation=dilation),
                nn.ReLU(), ResidualBlock(outc),
            ))
        for index, inc, outc in ((5, 384, 128), (4, 192, 64),
                                 (3, 96, 32), (2, 48, 16)):
            setattr(self, f'decoder{index}', nn.Sequential(
                nn.Conv2d(inc, outc, 3, padding=1),
                nn.ReLU(), ResidualBlock(outc),
            ))
        self.decoder1 = nn.Sequential(nn.Conv2d(16, 3, 3, padding=1), nn.Sigmoid())

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Restaure une tuile N × 3 × H × W, sans changement de résolution."""
        encoded = []
        for index in range(1, 6):
            x = getattr(self, f'encoder{index}')(x)
            encoded.append(x)
        for index in range(5, 1, -1):
            x = getattr(self, f'decoder{index}')(
                torch.cat((x, encoded[index - 2]), dim=1)
            )
        return self.decoder1(x)
