from __future__ import division
import torch
from torch.autograd import Variable
from torch.utils import data

import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init
import torch.utils.model_zoo as model_zoo
from torchvision import models

# general libs
import cv2
import matplotlib.pyplot as plt
from PIL import Image
import numpy as np
import math
import time
import tqdm
import os
import argparse
import copy


### My libs
from vos.datasets.DAVIS import DAVIS_MO_Test
from vos.models.STM import STM
from vos.algo.stm_train import STMAlgo
from vos.models.EMN import EMN
from vos.algo.emn_train import EMNAlgo


torch.set_grad_enabled(False) # Volatile

def get_arguments():
    parser = argparse.ArgumentParser(description="SST")
    parser.add_argument("-g", type=str, help="0; 0,1; 0,3; etc", required=True)
    parser.add_argument("-s", type=str, help="set", required=True)
    parser.add_argument("-y", type=int, help="year", required=True)
    parser.add_argument("-viz", help="Save visualization", action="store_true")
    parser.add_argument("-D", type=str, help="path to data",default='/local/DATA')
    parser.add_argument("-w", type=str, help="path to weight file", default="./data/weightfiles/STM_weights.pth")
    parser.add_argument("-o", type=str, help="path to save results", default="./data/STM_test/")
    return parser.parse_args()

debug = False
if debug:
    # configuration for remote attach and debug
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

args = get_arguments()

GPU = args.g
YEAR = args.y
SET = args.s
VIZ = args.viz
DATA_ROOT = args.D
OUTPUT_ROOT = args.o
PTH_PATH = args.w

# Model and version
MODEL = 'STM'
print(MODEL, ': Testing on DAVIS')

os.environ['CUDA_VISIBLE_DEVICES'] = GPU
if torch.cuda.is_available():
    print('using Cuda devices, num:', torch.cuda.device_count())

if VIZ:
    print('--- Produce mask overaid video outputs. Evaluation will run slow.')
    print('--- Require FFMPEG for encoding, Check folder ./viz')


palette = Image.open(DATA_ROOT + '/Annotations/480p/blackswan/00000.png').getpalette()

def Run_video(Fs, Ms, num_frames, num_objects, Mem_every=None, Mem_number=None):
    """ input Fs, Ms are both size (1, t, n, H, W)
    """

    # initialize storage tensors
    if Mem_every:
        to_memorize = [int(i) for i in np.arange(0, num_frames, step=Mem_every)]
    elif Mem_number:
        to_memorize = [int(round(i)) for i in np.linspace(0, num_frames, num=Mem_number+2)[:-1]]
    else:
        raise NotImplementedError

    Es = torch.zeros_like(Ms)
    Es[:,0] = Ms[:,0]

    for t in tqdm.tqdm(range(1, num_frames)):
        # memorize
        with torch.no_grad():
            prev_key, prev_value = model(Fs[:,t-1], Es[:,t-1], torch.tensor([num_objects])) 

        if t-1 == 0: # 
            this_keys, this_values = prev_key, prev_value # only prev memory
        else:
            this_keys = torch.cat([keys, prev_key], dim=3)
            this_values = torch.cat([values, prev_value], dim=3)
        
        # segment
        with torch.no_grad():
            logit = model(Fs[:,t], this_keys, this_values, torch.tensor([num_objects]))
        Es[:,t] = F.softmax(logit, dim=1)
        
        # update
        if t-1 in to_memorize:
            keys, values = this_keys, this_values
        
    pred = np.argmax(Es[0].cpu().numpy(), axis=1).astype(np.uint8)
    return pred, Es



Testset = DAVIS_MO_Test(DATA_ROOT, resolution='480p', imset='20{}/{}.txt'.format(YEAR,SET), single_object=(YEAR==16))
Testloader = data.DataLoader(Testset, batch_size=1, shuffle=False, num_workers=2 if not debug else 0, pin_memory=True)

model = nn.DataParallel(STM())
if torch.cuda.is_available():
    model.cuda()
model.eval() # turn-off BN
algo = STMAlgo() # only use its step() method, so no need for any hyper-parameters
algo.initialize(model)

print('Loading weights:', PTH_PATH)
model.load_state_dict(torch.load(PTH_PATH)["model_state_dict"])

code_name = '{}_DAVIS_{}{}'.format(MODEL,YEAR,SET)
print('Start Testing:', code_name)


for seq, V in enumerate(Testloader):
    Fs, Ms, num_objects, info = V
    seq_name = info['name'][0]
    num_frames = info['num_frames'][0].item()
    print('[{}]: num_frames: {}, num_objects: {}'.format(seq_name, num_frames, num_objects[0][0]))
    
    # pred, Es = Run_video(Fs, Ms, num_frames, num_objects, Mem_every=5, Mem_number=None)
    pred, _ = algo.step(
        frames= Fs,
        masks= Ms,
        n_objects= num_objects,
        Mem_every=5, Mem_number=None
    )
    pred = np.argmax(pred[0].detach().cpu().numpy(), axis= 1).astype(np.uint8)
        
    # Save results for quantitative eval ######################
    test_path = os.path.join(OUTPUT_ROOT, code_name, seq_name)
    if not os.path.exists(test_path):
        os.makedirs(test_path)
    for f in range(num_frames):
        img_E = Image.fromarray(pred[f])
        img_E.putpalette(palette)
        img_E.save(os.path.join(test_path, '{:05d}.png'.format(f)))

    if VIZ:
        from vos.utils.helpers import overlay_davis
        # visualize results #######################
        viz_path = os.path.join(OUTPUT_ROOT, 'viz/', code_name, seq_name)
        if not os.path.exists(viz_path):
            os.makedirs(viz_path)

        for f in range(num_frames):
            pF = (Fs[0,:,f].permute(1,2,0).numpy() * 255.).astype(np.uint8)
            pE = pred[f]
            canvas = overlay_davis(pF, pE, palette)
            canvas = Image.fromarray(canvas)
            canvas.save(os.path.join(viz_path, 'f{}.jpg'.format(f)))

        vid_path = os.path.join(OUTPUT_ROOT, 'viz/', code_name, '{}.mp4'.format(seq_name))
        frame_path = os.path.join(OUTPUT_ROOT, 'viz/', code_name, seq_name, 'f%d.jpg')
        os.system('ffmpeg -framerate 10 -i {} {} -vcodec libx264 -crf 10  -pix_fmt yuv420p  -nostats -loglevel 0 -y'.format(frame_path, vid_path))



