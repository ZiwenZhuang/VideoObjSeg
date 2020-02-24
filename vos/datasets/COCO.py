import torch
import torchvision
from torch.utils import data

from pycocotools.coco import COCO as COCOapi

import os.path as path
import skimage.io as io
import numpy as np

SUBSET_LEN = 50

class COCO(data.Dataset):
    def __init__(self, root,
            mode= "train", # choose between "train", "val"
            is_subset= False, # If is subset, the length will be a fixed small length
        ):
        self._root = root
        self._mode = mode
        self._is_subset = is_subset
        self.coco = COCOapi(
            path.join(self._root, "annotations/instances_{}2017.json".format(self._mode))
        )
        # load categories
        self._cats = self.coco.loadCats(self.coco.getCatIds())
        self._catNms = [cat['name'] for cat in self._cats]
        self._supNms = [cat['supercategory'] for cat in self._cats]
        self._output_mode = dict(catNms= None, is_supcats= False)

        # reset self mode to all categories
        self.set_cats()

    @property
    def n_objects(self):
        """ NOTE: this method depends on category mode (refer to set_cats).
        And background is also marked as an object
        """
        if self._output_mode["is_supcats"]:
            return len(self._supNms)+1
        else:
            return len(self._catNms)+1

    @property
    def all_categories(self):
        return self._cats

    @property
    def all_categories_names(self):
        return self._catNms

    @property
    def all_super_categories_names(self):
        return self._supNms

    def set_cats(self, cats: list= None, is_supcats= True):
        """ Given category, the dataset will only output all items from that dataset.
        And configure the output mask is in terms of supcats or cats.
        If not provided, all images will be output from __getitem__
        """
        self._output_mode["catNms"] = cats
        self._output_mode["is_supcats"] = is_supcats

        if self._output_mode["catNms"] is None:
            self.imgIds = self.coco.getImgIds()
        else:
            catNms = self._output_mode["catNms"]
            if self._output_mode["is_supcats"]:
                self.imgIds = self.coco.getImgIds(
                    imgIds= self.coco.getImgIds(),
                    catIds= self.coco.getCatIds(supNms= catNms)
                )
            else:
                self.imgIds = self.coco.getImgIds(
                    imgIds= self.coco.getImgIds(),
                    catIds= self.coco.getCatIds(catNms= catNms)
                )

    def __len__(self):
        if self._is_subset:
            return SUBSET_LEN
        else:
            return len(self.imgIds)

    def __getitem__(self, idx):
        if self._is_subset:
            idx = min(SUBSET_LEN, idx)

        img = self.coco.loadImgs(self.imgIds[idx])[0]
        # This image is in (H, W, C) shape
        image = io.imread(img["coco_url"])
        # transpose to (C, H, W) shape
        image = image.transpose(2, 0, 1)

        annIds = self.coco.getAnnIds(imgIds= img["id"])
        anns = self.coco.loadAnns(annIds)

        _, H, W = image.shape
        n_cats = len(self._supNms if self._output_mode["is_supcats"] else self._catNms)
        mask = np.empty((n_cats, H, W), dtype= np.uint8) # a background
        bg = np.ones((1, H, W), dtype= np.uint8)

        for ann in anns:
            cat = self._cats[ann["category_id"]]
            if self._output_mode["is_supcats"]:
                msk_idx = [i for i, name in enumerate(self._supNms) if name == cat["supercategory"]][0]
            else:
                msk_idx = [i for i, name in enumerate(self._supNms) if name == cat["name"]][0]
            ann_mask = self.coco.annToMask(ann)
            mask[msk_idx] |= ann_mask
            bg[0] &= (1-mask)
        mask = np.concatenate([bg, mask], axis= 0)

        return dict(
            image= image,
            mask= mask, # NOTE: 0-th dimension of mask is (n_cats+1)
            anns= anns,
        )

if __name__ == "__main__":
    # test code
    import ptvsd
    import sys
    ip_address = ('0.0.0.0', 5050)
    print("Process: " + " ".join(sys.argv[:]))
    print("Is waiting for attach at address: %s:%d" % ip_address, flush= True)
    # Allow other computers to attach to ptvsd at this IP address and port.
    ptvsd.enable_attach(address=ip_address, redirect_output= True)
    # Pause the program until a remote debugger is attached
    ptvsd.wait_for_attach()
    print("Process attached, start running into experiment...", flush= True)
    ptvsd.break_into_debugger()

    root = sys.argv[1]
    dataset = COCO(root)

    for i in range(len(dataset)):
        x = dataset[i]

    print("debug done...")
        

    