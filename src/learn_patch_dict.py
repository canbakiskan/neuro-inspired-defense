# learns the sparse dictionary by extracting patches from the train dataset

import numpy as np
from time import time
from sklearn.decomposition import MiniBatchDictionaryLearning
from torchvision.datasets import CIFAR10
from argparse import ArgumentParser
from os import path
from .utils.namers import dict_file_namer, dict_params_string
from .utils.get_modules import get_dictionary
from .parameters import get_arguments
from .utils.read_datasets import cifar10, tiny_imagenet, imagenette
import torch


def extract_patches(images, patch_shape, stride, in_order="NHWC", out_order="NHWC"):
    assert images.ndim >= 2 and images.ndim <= 4
    if isinstance(images, np.ndarray):
        from sklearn.feature_extraction.image import _extract_patches

        if images.ndim == 2:  # single gray image
            images = np.expand_dims(images, 0)

        if images.ndim == 3:
            if images.shape[2] == 3:  # single color image
                images = np.expand_dims(images, 0)
            else:  # multiple gray images or single gray image with first index 1
                images = np.expand_dims(images, 3)

        elif in_order == "NCHW":
            images = images.transpose(0, 2, 3, 1)
        # numpy expects order NHWC
        patches = _extract_patches(
            images,
            patch_shape=(1, *patch_shape),
            extraction_step=(1, stride, stride, 1),
        ).reshape(-1, *patch_shape)
        # now patches' shape = NHWC

        if out_order == "NHWC":
            pass
        elif out_order == "NCHW":
            patches = patches.permute(0, 3, 1, 2)
        else:
            raise ValueError(
                'out_order not understood (expected "NHWC" or "NCHW")')

    elif isinstance(images, torch.Tensor):
        if images.ndim == 2:  # single gray image
            images = images.unsqueeze(0)

        if images.ndim == 3:
            if images.shape[2] == 3:  # single color image
                images = images.unsqueeze(0)
            else:  # multiple gray image
                images = images.unsqueeze(3)

        if in_order == "NHWC":
            images = images.permute(0, 3, 1, 2)
        # torch expects order NCHW

        patches = torch.nn.functional.unfold(
            images, kernel_size=patch_shape[:2], stride=stride
        )

        # all these operations are done to circumvent pytorch's N,C,W,H ordering

        patches = patches.permute(0, 2, 1)
        nb_patches = patches.shape[0] * patches.shape[1]
        patches = patches.reshape(nb_patches, patch_shape[2], *patch_shape[:2])
        # now patches' shape = NCHW
        if out_order == "NHWC":
            patches = patches.permute(0, 2, 3, 1)
        elif out_order == "NCHW":
            pass
        else:
            raise ValueError(
                'out_order not understood (expected "NHWC" or "NCHW")')

    return patches

    # import matplotlib.pyplot as plt
    # plt.figure(figsize=(10, 10))
    # for i in range(10):
    #     for j in range(10):
    #         plt.subplot(10, 10, 10*i+j+1)
    #         plt.imshow(
    #             train_patches[10*i+j].reshape(*args.defense_patchshape))
    #         plt.xticks([])
    #         plt.yticks([])
    # plt.savefig("patches.pdf")
    # plt.close()

    # plt.figure(figsize=(5, 5))
    # plt.imshow(x_train[0])
    # plt.savefig("image.pdf")
    # plt.close()


def main():
    args = get_arguments()

    data_dir = args.directory + "data/"

    if args.dataset == "CIFAR10":
        train_loader, _ = cifar10(args)
    elif args.dataset == "Tiny-ImageNet":
        train_loader, _ = tiny_imagenet(args)
    elif args.dataset == "Imagenette":
        if not args.dict_online:
            args.train_batch_size = 9469
        train_loader, _ = imagenette(args)
    else:
        raise NotImplementedError

    dict_filepath = dict_file_namer(args)
    if path.exists(dict_filepath):
        print("Dictionary already learnt and saved.")
        dictionary_transpose = get_dictionary(args).t().numpy()
    else:
        if args.dict_online:  # this takes forever

            dico = MiniBatchDictionaryLearning(
                n_components=args.dict_nbatoms,
                alpha=args.dict_lambda,
                n_iter=args.dict_iter,
                batch_size=args.dict_batchsize,
                n_jobs=20,
            )

            t0 = time()
            for x_train, _ in train_loader:
                train_patches = extract_patches(
                    x_train, args.defense_patchshape, args.defense_stride, in_order="NCHW", out_order="NHWC"
                )
                train_patches = train_patches.reshape(
                    train_patches.shape[0], -1)

                dico.partial_fit(train_patches)
            dt = time() - t0
            print("done in %.2fs." % dt)

        else:
            if args.dataset == "CIFAR10":
                x_train = train_loader.dataset.data
                x_train = x_train / 255.0
            elif args.dataset == "Imagenette":

                from torchvision import transforms
                transform_train = transforms.Compose(
                    [
                        transforms.RandomCrop((160)),
                        transforms.RandomHorizontalFlip(),
                        transforms.ToTensor(),
                    ]
                )
                train_loader.dataset.transform = transform_train
                x_train = next(iter(train_loader))[0]
                x_train = x_train.permute(0, 2, 3, 1)
            # Extract all patches
            print("Extracting reference patches...")
            t0 = time()

            print("Images shape: {}".format(x_train.shape))
            train_patches = extract_patches(
                x_train, args.defense_patchshape, args.defense_stride, in_order="NHWC", out_order="NHWC"
            )
            print("Patches shape: {}".format(train_patches.shape))

            train_patches = train_patches.reshape(train_patches.shape[0], -1)

            print("done in %.2fs." % (time() - t0))

            print("Learning the dictionary...")
            t0 = time()
            dico = MiniBatchDictionaryLearning(
                n_components=args.dict_nbatoms,
                alpha=args.dict_lambda,
                n_iter=args.dict_iter,
                batch_size=args.dict_batchsize,
                n_jobs=20,
            )
            # we employ column notation i.e. each column is an atom.
            # but sklearn uses row notation i.e. each row is an atom.
            # so what we call dictionary is their components_.transpose()

            dico.fit(train_patches)

        dictionary_transpose = dico.components_
        dt = time() - t0
        print("done in %.2fs." % dt)

        np.savez(dict_filepath, dict=dico.components_,
                 params=dico.get_params())

    if args.dict_display:
        import matplotlib.pyplot as plt
        from .utils import plot_settings

        plt.figure(figsize=(11, 10))
        for i, atom in enumerate(dictionary_transpose[-400:]):
            plt.subplot(20, 20, i + 1)
            atom = atom.reshape(args.defense_patchshape)
            plt.imshow(
                (atom - atom.min()) / (atom.max() - atom.min()),
                interpolation="nearest",
            )

            # plt.axis("off")
            plt.xticks([])
            plt.yticks([])

        plt.suptitle(f"Dictionary learned from {args.dataset}", fontsize=16)
        plt.subplots_adjust(0.08, 0.02, 0.92, 0.85, 0.08, 0.23)

        figures_dir = args.directory + "figs/"

        dictionary_parameters_string = dict_params_string(args)
        plt.savefig(figures_dir + "dict_" +
                    dictionary_parameters_string + ".pdf")


if __name__ == "__main__":
    main()
