
# requires Python 3.5.3 :: Anaconda 4.4.0
# pip install opencv-python

import os
import sys
import argparse
import logging
import time
from pathlib import Path
import glob
import json

import numpy as np
from tqdm import tqdm
import cv2
import yaml
import airsim

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from edgetpumodel import EdgeTPUModel
from utils import resize_and_pad, get_image_tensor, save_one_json, coco80_to_coco91_class

# from pycoral.pybind._pywrap_coral import SetVerbosity as set_verbosity
# set_verbosity(10)

if __name__ == "__main__":

    parser = argparse.ArgumentParser("EdgeTPU test runner")
    parser.add_argument("--model", "-m", help="weights file", required=True)
    parser.add_argument("--bench_speed", action='store_true', help="run speed test on dummy data")
    parser.add_argument("--bench_image", action='store_true', help="run detection test")
    parser.add_argument("--bench_airsim", action='store_true', help="run detection on airsim")
    parser.add_argument("--conf_thresh", type=float, default=0.25, help="model confidence threshold")
    parser.add_argument("--iou_thresh", type=float, default=0.45, help="NMS IOU threshold")
    parser.add_argument("--names", type=str, default='data/coco.yaml', help="Names file")
    parser.add_argument("--image", "-i", type=str, help="Image file to run detection on")
    parser.add_argument("--device", type=int, default=0, help="Image capture device to run live detection")
    parser.add_argument("--stream", action='store_true', help="Process a stream")
    parser.add_argument("--bench_coco", action='store_true', help="Process a stream")
    parser.add_argument("--coco_path", type=str, help="Path to COCO 2017 Val folder")
    parser.add_argument("--quiet", "-q", action='store_true', help="Disable logging (except errors)")

    args = parser.parse_args()

    if args.quiet:
        logging.disable(logging.CRITICAL)
        logger.disabled = True

    if args.stream and args.image:
        logger.error("Please select either an input image or a stream")
        exit(1)

    model = EdgeTPUModel(args.model, args.names, conf_thresh=args.conf_thresh, iou_thresh=args.iou_thresh)
    input_size = model.get_image_size()

    x = (255 * np.random.random((3, *input_size))).astype(np.int8)
    model.forward(x)

    conf_thresh = 0.25
    iou_thresh = 0.45
    classes = None
    agnostic_nms = False
    max_det = 1000

    if args.bench_speed:
        logger.info("Performing test run")
        n_runs = 100

        inference_times = []
        nms_times = []
        total_times = []

        for i in tqdm(range(n_runs)):
            x = (255 * np.random.random((3, *input_size))).astype(np.float32)

            pred = model.forward(x)
            tinference, tnms = model.get_last_inference_time()

            inference_times.append(tinference)
            nms_times.append(tnms)
            total_times.append(tinference + tnms)

        inference_times = np.array(inference_times)
        nms_times = np.array(nms_times)
        total_times = np.array(total_times)

        logger.info("Inference time (EdgeTPU): {:1.2f} +- {:1.2f} ms".format(inference_times.mean() / 1e-3,
                                                                             inference_times.std() / 1e-3))
        logger.info("NMS time (CPU): {:1.2f} +- {:1.2f} ms".format(nms_times.mean() / 1e-3, nms_times.std() / 1e-3))
        fps = 1.0 / total_times.mean()
        logger.info("Mean FPS: {:1.2f}".format(fps))

    elif args.bench_image:
        logger.info("Testing on Zidane image")
        model.predict("./data/images/zidane.jpg")

    elif args.bench_airsim:
        logger.info("Testing on Zidane image")
        client = airsim.MultirotorClient()

        # because this method returns std::vector<uint8>, msgpack decides to encode it as a string unfortunately.
        while(True):
            rawImage = client.simGetImage("3", airsim.ImageType.Scene)
            if (rawImage == None):
                print("Camera is not returning image, please check airsim for error messages")
                sys.exit(0)
            else:
                png = cv2.imdecode(airsim.string_to_uint8_array(rawImage), cv2.IMREAD_UNCHANGED)
                cv2.imwrite("./data/images/zidane.jpg", png)
                model.predict("./data/images/zidane.jpg")

            key = cv2.waitKey(1) & 0xFF
            if (key == 27 or key == ord('q') or key == ord('x')):
                break





    elif args.bench_coco:
        logger.info("Testing on COCO dataset")

        model.conf_thresh = 0.001
        model.iou_thresh = 0.65

        coco_glob = os.path.join(args.coco_path, "*.jpg")
        images = glob.glob(coco_glob)

        logger.info("Looking for: {}".format(coco_glob))
        ids = [int(os.path.basename(i).split('.')[0]) for i in images]

        out_path = "./coco_eval"
        os.makedirs("./coco_eval", exist_ok=True)

        logger.info("Found {} images".format(len(images)))

        class_map = coco80_to_coco91_class()

        predictions = []

        for image in tqdm(images):
            res = model.predict(image, save_img=False, save_txt=False)
            save_one_json(res, predictions, Path(image), class_map)

        pred_json = os.path.join(out_path,
                                 "{}_predictions.json".format(os.path.basename(args.model)))

        with open(pred_json, 'w') as f:
            json.dump(predictions, f, indent=1)

    elif args.image is not None:
        logger.info("Testing on user image: {}".format(args.image))
        model.predict(args.image)

    elif args.stream:
        logger.info("Opening stream on device: {}".format(args.device))

        cam = cv2.VideoCapture(args.device)

        while True:
            try:
                res, image = cam.read()

                if res is False:
                    logger.error("Empty image received")
                    break
                else:
                    full_image, net_image, pad = get_image_tensor(image, input_size[0])
                    pred = model.forward(net_image)

                    model.process_predictions(pred[0], full_image, pad)

                    tinference, tnms = model.get_last_inference_time()
                    logger.info("Frame done in {}".format(tinference + tnms))
            except KeyboardInterrupt:
                break

        cam.release()







