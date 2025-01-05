import torch
import torch.nn as nn
import torch.nn.functional as F
import random
import numpy as np
from .utils import ModelOutputs

def initialize_weights(module):
    for m in module.modules():
        if isinstance(m,nn.Linear):
            # ref from clam
            nn.init.xavier_normal_(m.weight)
            if m.bias is not None:
                m.bias.data.zero_()
        elif isinstance(m,nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

class DAttention(nn.Module):
    def __init__(self, d_in, n_classes, dropout=True, act='gelu'):
        super(DAttention, self).__init__()
        self.L = 512
        self.D = 128
        self.K = 1
        self.feature = [nn.Linear(d_in, 512)]
        
        if act.lower() == 'gelu':
            self.feature += [nn.GELU()]
        else:
            self.feature += [nn.ReLU()]

        if dropout:
            self.feature += [nn.Dropout(0.25)]

        self.feature = nn.Sequential(*self.feature)

        self.attention = nn.Sequential(
            nn.Linear(self.L, self.D),
            nn.Tanh(),
            nn.Linear(self.D, self.K)
        )
        self.classifier = nn.Sequential(
            nn.Linear(self.L*self.K, n_classes),
        )


        # self.apply(initialize_weights)


    def forward(self, x):
        feature = self.feature(x)
        feature = feature.squeeze()
        A = self.attention(feature)
        A = torch.transpose(A, -1, -2)  # KxN
        A_raw = A
        A = F.softmax(A, dim=-1)  # softmax over N
        M = torch.mm(A, feature)  # KxL
        
        logits = self.classifier(M)
        y_hat = torch.argmax(logits, dim=1)
        y_prob = F.softmax(logits, dim=1)
        
        return ModelOutputs(features=M, logits=logits, y_hat=y_hat, y_prob=y_prob)


class GatedAttention(nn.Module):
    def __init__(self, d_in, n_classes, dropout=True, act='gelu'):
        super(GatedAttention, self).__init__()
        self.L = 512
        self.D = 128
        self.K = 1
        self.feature = [nn.Linear(d_in, 512)]
        if act.lower() == 'gelu':
            self.feature += [nn.GELU()]
        else:
            self.feature += [nn.ReLU()]

        if dropout:
            self.feature += [nn.Dropout(0.25)]

        self.feature = nn.Sequential(*self.feature)

        self.attention_V = nn.Sequential(
            nn.Linear(self.L, self.D),
            nn.Tanh()
        )

        self.attention_U = nn.Sequential(
            nn.Linear(self.L, self.D),
            nn.Sigmoid()
        )

        self.attention_weights = nn.Linear(self.D, self.K)

        self.classifier = nn.Sequential(
            nn.Linear(self.L*self.K, n_classes),
        )

    def forward(self, x):
        feature = self.feature(x)
        feature = feature.squeeze()

        A_V = self.attention_V(feature)  # NxD
        A_U = self.attention_U(feature)  # NxD
        A = self.attention_weights(A_V * A_U) # element wise multiplication # NxK
        A = torch.transpose(A, 1, 0)  # KxN
        A = F.softmax(A, dim=1)  # softmax over N

        M = torch.mm(A, feature)  # KxL

        logits = self.classifier(M)
        y_hat = torch.argmax(logits, dim=1)
        y_prob = F.softmax(logits, dim=1)
                
        return ModelOutputs(features=M, logits=logits, y_hat=y_hat, y_prob=y_prob)



if __name__ == '__main__':
    seed_value = 42
    random.seed(seed_value)
    np.random.seed(seed_value)
    torch.manual_seed(seed_value)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed_value)
    model = DAttention(1024, 2, dropout=False, act='relu')