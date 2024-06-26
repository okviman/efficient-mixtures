import torch.nn as nn
import torch.nn.functional as func
import torch
from models.conv_nets import GatedConv2d
from models.load_pretrained_model import load_resnet


class GatedConv2dEncoder(nn.Module):
    def __init__(self, n_dims, S, latent_dims, h=294, activation=nn.ReLU, device='cuda:0'):
        super().__init__()
        self.device = device
        self.activation = activation
        self.h = h
        self.S = S

        self.conv_layer = nn.Sequential(
            GatedConv2d(1, 32, 7, 1, 3),
            GatedConv2d(32, 32, 3, 2, 1),
            GatedConv2d(32, 64, 5, 1, 2),
            GatedConv2d(64, 64, 3, 2, 1),
            GatedConv2d(64, 6, 3, 1, 1)
        )

        self.mu_enc = nn.Linear(in_features=h + self.S, out_features=latent_dims)
        self.log_var_enc = nn.Sequential(
            nn.Linear(in_features=h + self.S, out_features=latent_dims),
            nn.Hardtanh(min_val=-6., max_val=2.)
        )

    def forward(self, x, component):
        # component = one-hot encoding
        x = self.conv_layer(x)
        x = x.view((-1, self.h))
        x = torch.cat((x, component), dim=-1)
        mu = self.mu_enc(x)
        std = torch.exp(0.5 * self.log_var_enc(x))
        return mu, std


class GatedConv2dResidualEncoder(nn.Module):
    def __init__(self, n_dims, S, latent_dims, h=294, activation=nn.ReLU, device='cuda:0', conv_layer=True):
        super().__init__()
        self.device = device
        self.activation = activation
        self.h = h
        self.S = S

        if conv_layer:
            self.conv_layer = nn.Sequential(
                GatedConv2d(1, 32, 7, 1, 3),
                GatedConv2d(32, 32, 3, 2, 1),
                GatedConv2d(32, 64, 5, 1, 2),
                GatedConv2d(64, 64, 3, 2, 1),
                GatedConv2d(64, 6, 3, 1, 1)
            )

        self.mu_0 = nn.Sequential(
            nn.Linear(in_features=h + self.S, out_features=latent_dims),
            nn.ReLU()
        )
        self.mu_1 = nn.Sequential(
            nn.Linear(in_features=h + self.S + latent_dims, out_features=latent_dims),
            nn.ReLU()
        )
        self.mu_enc = nn.Linear(in_features=h + self.S + latent_dims, out_features=latent_dims)

        self.log_var_0 = nn.Sequential(
            nn.Linear(in_features=h + self.S, out_features=latent_dims),
            nn.ReLU()
        )
        self.log_var_1 = nn.Sequential(
            nn.Linear(in_features=h + self.S + latent_dims, out_features=latent_dims),
            nn.ReLU()
        )
        self.log_var_enc = nn.Sequential(
            nn.Linear(in_features=h + self.S + latent_dims, out_features=latent_dims),
            nn.Hardtanh(min_val=-6., max_val=2.)
        )

    def forward(self, x_s):
        # x_s = representation before parameterization net + component masking
        x_mu = self.mu_0(x_s)
        x_mu = torch.cat((x_s, x_mu), dim=-1)
        x_mu = self.mu_1(x_mu)
        x_mu = torch.cat((x_s, x_mu), dim=-1)
        mu = self.mu_enc(x_mu)

        x_std = self.log_var_0(x_s)
        x_std = torch.cat((x_s, x_std), dim=-1)
        x_std = self.log_var_1(x_std)
        x_std = torch.cat((x_s, x_std), dim=-1)
        std = torch.exp(0.5 * self.log_var_enc(x_std))
        return mu, std


class EnsembleGatedConv2dEncoders(nn.Module):
    def __init__(self, n_dims, latent_dims, h=294, S=2, residuals=True, activation=nn.ReLU, device='cuda:0',
                 cifar=False):
        super().__init__()
        self.device = device
        self.S = S
        self.latent_dims = latent_dims
        self.residuals = residuals
        if residuals:
            self.encoder = GatedConv2dResidualEncoder(n_dims, S, latent_dims, h=h, activation=activation, device=device)
        else:
            self.encoder = GatedConv2dEncoder(n_dims, S, latent_dims, h=h, activation=activation, device=device)

    def forward(self, x, components):
        # (S, ) = components.shape, components == 1 indicates which component to use
        n_A = torch.sum(components)
        mu = torch.zeros((x.size(0), n_A.int(), self.latent_dims), device=self.device)
        std = torch.zeros_like(mu)
        x = self.encoder.conv_layer(x)
        x = x.view((-1, self.encoder.h))
        for i, s in enumerate(torch.where(components == 1)[0]):
            component = func.one_hot(s, self.S).view((1, -1)).tile((x.size(0), 1))
            x_s = torch.cat((x, component), dim=-1)
            if self.residuals:
                mu[:, i, :], std[:, i, :] = self.encoder(x_s)
            else:
                mu[:, i, :], std[:, i, :] = self.encoder.mu_enc(x_s), torch.exp(0.5 * self.encoder.log_var_enc(x_s))
        return mu, std


class ResNetEncoder(GatedConv2dResidualEncoder):
    def __init__(self, S, latent_dims, device='cuda:0', resnet_model='resnet1202'):
        super().__init__(n_dims=None, S=S, h=64, latent_dims=latent_dims, device=device, conv_layer=False)
        self.S = S
        self.latent_dims = latent_dims
        self.conv_layer = load_resnet(resnet_model, device)
        # the output dims of the pretrained resnet is 64


class EnsembleResnetEncoders(EnsembleGatedConv2dEncoders):
    def __init__(self, latent_dims, S=2, device='cuda:0', resnet_model='resnet20'):
        super().__init__(n_dims=None, latent_dims=latent_dims, h=64, S=S, device=device)
        self.encoder = ResNetEncoder(S, latent_dims, device, resnet_model)

    def forward(self, x, components):
        # (S, ) = components.shape, components == 1 indicates which component to use
        x = self.encoder.conv_layer(x)
        n_A = torch.sum(components)
        mu = torch.zeros((x.size(0), n_A.int(), self.latent_dims), device=self.device)
        std = torch.zeros_like(mu)

        for i, s in enumerate(torch.where(components == 1)[0]):
            component = func.one_hot(s, self.S).view((1, -1)).tile((x.size(0), 1))
            x_s = torch.cat((x, component), dim=-1)
            if self.residuals:
                mu[:, i, :], std[:, i, :] = self.encoder(x_s)
            else:
                mu[:, i, :], std[:, i, :] = self.encoder.mu_enc(x_s), torch.exp(0.5 * self.encoder.log_var_enc(x_s))
        return mu, std
