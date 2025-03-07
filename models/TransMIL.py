import torch
import torch.nn as nn
import numpy as np
from nystrom_attention import NystromAttention
from .utils import ModelOutputs
import torch.nn.functional as F


class TransLayer(nn.Module):

    def __init__(self, norm_layer=nn.LayerNorm, dim=512):
        super().__init__()
        self.norm = norm_layer(dim)
        self.attn = NystromAttention(
            dim = dim,
            dim_head = dim//8,
            heads = 8,
            num_landmarks = dim//2,    # number of landmarks
            pinv_iterations = 6,    # number of moore-penrose iterations for approximating pinverse. 6 was recommended by the paper
            residual = True,         # whether to do an extra residual with the value or not. supposedly faster convergence if turned on
            dropout=0.1
        )

    def forward(self, x):
        x = x + self.attn(self.norm(x))

        return x


class PPEG(nn.Module):
    def __init__(self, dim=512):
        super(PPEG, self).__init__()
        self.proj = nn.Conv2d(dim, dim, 7, 1, 7//2, groups=dim)
        self.proj1 = nn.Conv2d(dim, dim, 5, 1, 5//2, groups=dim)
        self.proj2 = nn.Conv2d(dim, dim, 3, 1, 3//2, groups=dim)

    def forward(self, x, H, W):
        B, _, C = x.shape
        cls_token, feat_token = x[:, 0], x[:, 1:]
        cnn_feat = feat_token.transpose(1, 2).view(B, C, H, W)
        x = self.proj(cnn_feat)+cnn_feat+self.proj1(cnn_feat)+self.proj2(cnn_feat)
        x = x.flatten(2).transpose(1, 2)
        x = torch.cat((cls_token.unsqueeze(1), x), dim=1)
        return x


# TransMIL
class TransMIL(nn.Module):
    def __init__(self, d_in, d_model, n_classes, surv_classes=4, task='cls'):
        super(TransMIL, self).__init__()
        self.proj = PPEG(dim=d_model)
        self._fc1 = nn.Sequential(nn.Linear(d_in, d_model), nn.ReLU())
        self.cls_token = nn.Parameter(torch.randn(1, 1, d_model))
        self.pos_layer = PPEG(dim=d_model)
        self.layer1 = TransLayer(dim=d_model)
        self.layer2 = TransLayer(dim=d_model)
        self.norm = nn.LayerNorm(d_model)
        self.classifier = nn.Linear(d_model, n_classes)
        if 'cls' in task and 'surv' in task:
            self.hazard_layer = nn.Linear(d_model, surv_classes)
        self.task = task
    
    def encode(self, data):
        # the shared forward process for both classification and survival tasks
        # return the features (B, C) before the last linear layer
        # x: [B, N, D]
        x = data['x']
        h = self._fc1(x)
        
        #---->pad
        H = h.shape[1]
        _H, _W = int(np.ceil(np.sqrt(H))), int(np.ceil(np.sqrt(H)))
        add_length = _H * _W - H
        h = torch.cat([h, h[:,:add_length,:]],dim = 1) #[B, N, 512]

        #---->cls_token
        B = h.shape[0]
        cls_tokens = self.cls_token.expand(B, -1, -1).cuda()
        h = torch.cat((cls_tokens, h), dim=1)

        #---->Translayer x1
        h = self.layer1(h) #[B, N, 512]

        #---->PPEG
        h = self.pos_layer(h, _H, _W) #[B, N, 512]
        
        #---->Translayer x2
        h = self.layer2(h) #[B, N, 512]

        #---->cls_token
        features = self.norm(h)[:,0] # [B, 512]

        return features

    def cls_forward(self, data):
        features = self.encode(data)
        logits = self.classifier(features)
        y_hat = torch.argmax(logits, dim=1)
        y_prob = F.softmax(logits, dim=1)
        return ModelOutputs(features=features, logits=logits, y_hat=y_hat, y_prob=y_prob)

    def surv_forward(self, data):
        features = self.encode(data)
        logits = self.classifier(features) # n_classes = surv_classes if task == surv
        
        y_hat = torch.argmax(logits, dim=1)
        hazards = torch.sigmoid(logits)
        surv = torch.cumprod(1 - hazards, dim=1)
        return ModelOutputs(features=features, logits=logits, hazards=hazards, surv=surv, y_hat=y_hat)

    def multitask_forward(self, data):
        features = self.encode(data)
        cls_logits = self.classifier(features)
        y_hat = torch.argmax(cls_logits, dim=1)
        y_prob = F.softmax(cls_logits, dim=1)

        # survival task
        surv_logits = self.hazard_layer(features)
        surv_y_hat = torch.argmax(surv_logits, dim=1)
        hazards = torch.sigmoid(surv_logits)
        surv = torch.cumprod(1 - hazards, dim=1)
        return ModelOutputs(features=features, cls_logits=cls_logits, y_hat=y_hat, y_prob=y_prob,
                            surv_logits=surv_logits, surv_y_hat=surv_y_hat, hazards=hazards, surv=surv)

    def forward(self, data):
        if 'cls' in self.task and 'surv' in self.task:
            return self.multitask_forward(data)
        elif 'cls' in self.task:
            return self.cls_forward(data)
        elif 'surv' in self.task:
            return self.surv_forward(data)
        else:
            raise NotImplementedError
