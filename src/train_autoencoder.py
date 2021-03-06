import torch
import torch.optim as optim
import torch.backends.cudnn as cudnn

import os
import numpy as np
import logging

from .train_test_functions import (
    train_autoencoder_unsupervised,
    test_autoencoder_unsupervised,
)
from .parameters import get_arguments
from .utils.read_datasets import cifar10, tiny_imagenet, imagenette
from .models.autoencoders import *
from tqdm import tqdm
from .utils.namers import (
    autoencoder_ckpt_namer,
    autoencoder_log_namer,
)
from torchvision import datasets, transforms
import sys

logger = logging.getLogger(__name__)


def main():
    args = get_arguments()
    if args.autoencoder_train_supervised:
        print("Use train_classifier.py for supervised training of autoencoder.")
        exit()

    logging.basicConfig(
        format="[%(asctime)s] - %(message)s",
        datefmt="%Y/%m/%d %H:%M:%S",
        level=logging.INFO,
        handlers=[
            logging.FileHandler(autoencoder_log_namer(args)),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger.info(args)
    logger.info("\n")

    # Get same results for each training with same parameters
    torch.manual_seed(args.seed)

    use_cuda = not args.no_cuda and torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    x_min = 0.0
    x_max = 1.0

    if args.dataset == "CIFAR10":
        train_loader, test_loader = cifar10(args)
    elif args.dataset == "Tiny-ImageNet":
        train_loader, test_loader = tiny_imagenet(args)
    elif args.dataset == "Imagenette":
        train_loader, test_loader = imagenette(args)
    else:
        raise NotImplementedError

    autoencoder = autoencoder_dict[args.autoencoder_arch](args).to(device)
    autoencoder.train()

    if device == "cuda":
        autoencoder = torch.nn.DataParallel(autoencoder)
        cudnn.benchmark = True

    if args.optimizer == "sgd":
        optimizer = optim.SGD(
            autoencoder.parameters(),
            lr=args.lr,
            momentum=args.momentum,
            weight_decay=args.weight_decay,
        )
    elif args.optimizer == "rms":
        optimizer = optim.RMSprop(
            autoencoder.parameters(),
            lr=args.lr,
            weight_decay=args.weight_decay,
            momentum=args.momentum)

    elif args.optimizer == "adam":
        optimizer = optim.Adam(
            autoencoder.parameters(),
            lr=args.lr,
            weight_decay=args.weight_decay,
        )
    else:
        raise NotImplementedError

    if args.lr_scheduler == "cyc":
        lr_steps = args.classifier_epochs * len(train_loader)
        scheduler = torch.optim.lr_scheduler.CyclicLR(
            optimizer,
            base_lr=args.lr_min,
            max_lr=args.lr_max,
            step_size_up=lr_steps / 2,
            step_size_down=lr_steps / 2,
        )
    elif args.lr_scheduler == "step":
        scheduler = torch.optim.lr_scheduler.MultiStepLR(
            optimizer,
            milestones=[35],
            gamma=0.1)

    elif args.lr_scheduler == "mult":
        def lr_fun(epoch):
            if epoch % 3 == 0:
                return 0.962
            else:
                return 1.0

        scheduler = MultiplicativeLR(optimizer, lr_fun)
    else:
        raise NotImplementedError

    with tqdm(
        total=args.autoencoder_epochs,
        initial=0,
        unit="ep",
        unit_scale=True,
        unit_divisor=1000,
        leave=True,
        bar_format="{percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}{postfix}]",
    ) as pbar:
        for epoch in range(args.autoencoder_epochs):

            train_loss = train_autoencoder_unsupervised(
                autoencoder, train_loader, optimizer, scheduler
            )
            validation_loss = test_autoencoder_unsupervised(
                autoencoder, test_loader)

            logger.info(f"Epoch: {epoch}, Train Loss: {train_loss}")
            logger.info(f"Epoch: {epoch}, Validation Loss: {validation_loss}")

            pbar.set_postfix(
                Val_Loss=f"{validation_loss:.4f}", refresh=True,
            )
            pbar.update(1)

    if args.save_checkpoint:

        if not os.path.exists(args.directory + "checkpoints/"):
            os.makedirs(args.directory + "checkpoints/")

        autoencoder_filepath = autoencoder_ckpt_namer(args)
        torch.save(
            autoencoder.state_dict(), autoencoder_filepath,
        )

        logger.info(f"Saved to {autoencoder_filepath}")


if __name__ == "__main__":
    main()
