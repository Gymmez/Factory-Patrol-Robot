#!/usr/bin/env bash
set -euo pipefail

mkdir -p ~/miniconda3
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm -rf ~/miniconda3/miniconda.sh
export CONDA_ALWAYS_YES=true
~/miniconda3/bin/conda init bash
~/miniconda3/bin/conda init zsh

source ~/miniconda3/etc/profile.d/conda.sh
conda config --set remote_connect_timeout_secs 60
conda config --set remote_read_timeout_secs 120
conda config --set remote_max_retries 5
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
conda create --yes --name gauge_reader python=3.8
conda activate gauge_reader



conda install --yes pytorch==2.0.0 torchvision==0.15.0 torchaudio==2.0.0 -c pytorch -c nvidia

pip install -U openmim
mim install mmengine==0.7.2
mim install mmcv==2.0.0
mim install mmdet==3.0.0
mim install mmocr==1.0.0

pip install ultralytics


pip install -U scikit-learn
