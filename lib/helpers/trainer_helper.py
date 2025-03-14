import os
import tqdm

import torch
import numpy as np
import torch.nn as nn

from lib.helpers.save_helper import get_checkpoint_state
from lib.helpers.save_helper import load_checkpoint
from lib.helpers.save_helper import save_checkpoint
from lib.losses.centernet_loss import compute_centernet3d_loss

import wandb

class Trainer(object):
    def __init__(self,
                 cfg,
                 model,
                 optimizer,
                 train_loader,
                 test_loader,
                 lr_scheduler,
                 warmup_lr_scheduler,
                 logger):
        self.cfg = cfg
        self.model = model
        self.optimizer = optimizer
        self.train_loader = train_loader
        self.test_loader = test_loader
        self.lr_scheduler = lr_scheduler
        self.warmup_lr_scheduler = warmup_lr_scheduler
        self.logger = logger
        self.epoch = 0
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # loading pretrain/resume model
        if cfg.get('pretrain_model'):
            assert os.path.exists(cfg['pretrain_model'])
            load_checkpoint(model=self.model,
                            optimizer=None,
                            filename=cfg['pretrain_model'],
                            map_location=self.device,
                            logger=self.logger)

        if cfg.get('resume_model', None):
            assert os.path.exists(cfg['resume_model'])
            self.epoch = load_checkpoint(model=self.model.to(self.device),
                                         optimizer=self.optimizer,
                                         filename=cfg['resume_model'],
                                         map_location=self.device,
                                         logger=self.logger)
            self.lr_scheduler.last_epoch = self.epoch - 1

        self.gpu_ids = [0] #list(map(int, cfg['gpu_ids'].split(',')))
        self.model = torch.nn.DataParallel(model, device_ids=self.gpu_ids).to(self.device)

        wandb.init(project="Monodle_fish",entity='alfin-nurhalim')


    def train(self):
        start_epoch = self.epoch

        progress_bar = tqdm.tqdm(range(start_epoch, self.cfg['max_epoch']), dynamic_ncols=True, leave=True, desc='epochs')
        for epoch in range(start_epoch, self.cfg['max_epoch']):
            # reset random seed
            # ref: https://github.com/pytorch/pytorch/issues/5059
            np.random.seed(np.random.get_state()[1][0] + epoch)
            # train one epoch
            self.train_one_epoch()
            self.epoch += 1

            # update learning rate
            if self.warmup_lr_scheduler is not None and epoch < 5:
                self.warmup_lr_scheduler.step()
            else:
                self.lr_scheduler.step()


            # save trained model
            if (self.epoch % self.cfg['save_frequency']) == 0:
                os.makedirs('checkpoints', exist_ok=True)
                ckpt_name = os.path.join('checkpoints', 'checkpoint_epoch_%d' % self.epoch)
                save_checkpoint(get_checkpoint_state(self.model, self.optimizer, self.epoch), ckpt_name)

            progress_bar.update()

        return None

    def train_one_epoch(self):
        self.model.train()
        progress_bar = tqdm.tqdm(total=len(self.train_loader), leave=(self.epoch+1 == self.cfg['max_epoch']), desc='iters')

        logger = dict()
        logger['center'] = list()
        logger['hm'] = list()
        logger['depth'] = list()
        logger['size3d'] = list()
        logger['rotation'] = list()

        for batch_idx, (inputs, targets, _) in enumerate(self.train_loader):
            inputs = inputs.to(self.device)
            for key in targets.keys():
                targets[key] = targets[key].to(self.device)

            # train one batch
            self.optimizer.zero_grad()
            outputs = self.model(inputs)
            total_loss, stats_batch = compute_centernet3d_loss(outputs, targets)
            total_loss.backward()
            self.optimizer.step()

            progress_bar.update()

            logger['center'].append(stats_batch['seg'])
            logger['hm'].append(stats_batch['size2d'])
            logger['depth'].append(stats_batch['depth'])
            logger['size3d'].append(stats_batch['size3d'])
            logger['rotation'].append(stats_batch['heading'])

        logger['center'] = sum(logger['center'])/len(logger['center'])
        logger['hm'] = sum(logger['hm'])/len(logger['hm'])
        logger['depth'] = sum(logger['depth'])/len(logger['depth'])
        logger['size3d'] = sum(logger['size3d'])/len(logger['size3d'])
        logger['rotation'] = sum(logger['rotation'])/len(logger['rotation'])
        
        wandb.log(logger)
        progress_bar.close()




