import torch
import cv2
import os
import numpy as np
import shutil
import moviepy.video.io.ffmpeg_writer as ffmpeg_writer
from moviepy.video.io.VideoFileClip import VideoFileClip
from modeling.anime_gan import Generator
from utils.common import load_weight
from utils.image_processing import resize_image, normalize_input, denormalize_input
from utils import read_image
from tqdm import tqdm


cuda_available = torch.cuda.is_available()

VALID_FORMATS = {
    'jpeg', 'jpg', 'jpe',
    'png', 'bmp',
}

class Transformer:
    def __init__(self, weight='hayao', add_mean=False):
        self.G = Generator()

        if cuda_available:
            self.G = self.G.cuda()

        load_weight(self.G, weight)
        self.G.eval()

        print("Weight loaded, ready to predict")

    def transform(self, image):
        '''
        Transform a image to animation

        @Arguments:
            - image: np.array, shape = (Batch, width, height, channels)

        @Returns:
            - anime version of image: np.array
        '''
        with torch.no_grad():
            fake = self.G(self.preprocess_images(image))
            fake = fake.detach().cpu().numpy()
            # Channel last
            fake = fake.transpose(0, 2, 3, 1)
            return fake

    def transform_file(self, file_path, save_path):
        if not save_path.endswith('png'):
            raise ValueError(f"{save_path} should be png format")

        image = read_image(file_path)

        if image is None:
            raise ValueError(f"Could not get image from {file_path}")

        anime_img = self.transform(resize_image(image))[0]
        anime_img = denormalize_input(anime_img, dtype=np.int16)
        cv2.imwrite(save_path, anime_img[..., ::-1])
        print(f"Anime image saved to {save_path}")

    def transform_in_dir(self, img_dir, dest_dir, max_images=0, img_size=(512, 512)):
        '''
        Read all images from img_dir, transform and write the result
        to dest_dir

        '''
        os.makedirs(dest_dir, exist_ok=True)

        files = os.listdir(img_dir)
        files = [f for f in files if self.is_valid_file(f)]
        print(f'Found {len(files)} images in {img_dir}')

        if max_images:
            files = files[:max_images]

        for fname in tqdm(files):
            image = cv2.imread(os.path.join(img_dir, fname))[:,:,::-1]
            image = resize_image(image)
            anime_img = self.transform(image)[0]
            ext = fname.split('.')[-1]
            fname = fname.replace(f'.{ext}', '')
            anime_img = denormalize_input(anime_img, dtype=np.int16)
            cv2.imwrite(os.path.join(dest_dir, f'{fname}_anime.jpg'), anime_img[..., ::-1])

    def transform_video(self, input_path, output_path, batch_size=4, start=0, end=0):
        '''
        Transform a video to animation version
        https://github.com/lengstrom/fast-style-transfer/blob/master/evaluate.py#L21
        '''
        # Force to None
        end = end or None

        if not os.path.isfile(input_path):
            raise FileNotFoundError(f'{input_path} does not exist')

        output_dir = "/".join(output_path.split("/")[:-1])
        os.makedirs(output_dir, exist_ok=True)
        is_gg_drive = '/drive/' in output_path
        temp_file = ''

        if is_gg_drive:
            # Writing directly into google drive can be inefficient
            temp_file = f'tmp_anime.{output_path.split(".")[-1]}'

        def transform_and_write(frames, count, writer):
            anime_images = denormalize_input(self.transform(frames), dtype=np.uint8)
            for i in range(0, count):
                img = np.clip(anime_images[i], 0, 255)
                writer.write_frame(img)

        video_clip = VideoFileClip(input_path, audio=False)
        if start or end:
            video_clip = video_clip.subclip(start, end)

        video_writer = ffmpeg_writer.FFMPEG_VideoWriter(
            temp_file or output_path,
            video_clip.size, video_clip.fps, codec="libx264",
            preset="medium", bitrate="2000k",
            audiofile=input_path, threads=None,
            ffmpeg_params=None)

        total_frames = round(video_clip.fps * video_clip.duration)
        print(f'Transfroming video {input_path}, {total_frames} frames, size: {video_clip.size}')

        batch_shape = (batch_size, video_clip.size[1], video_clip.size[0], 3)
        frame_count = 0
        frames = np.zeros(batch_shape, dtype=np.float32)
        for frame in tqdm(video_clip.iter_frames()):
            try:
                frames[frame_count] = frame
                frame_count += 1
                if frame_count == batch_size:
                    transform_and_write(frames, frame_count, video_writer)
                    frame_count = 0
            except Exception as e:
                print(e)
                break

        # The last frames
        if frame_count != 0:
            transform_and_write(frames, frame_count, video_writer)

        if temp_file:
            # move to output path
            shutil.move(temp_file, output_path)

        print(f'Animation video saved to {output_path}')
        video_writer.close()

    def preprocess_images(self, images):
        '''
        Preprocess image for inference

        @Arguments:
            - images: np.ndarray

        @Returns
            - images: torch.tensor
        '''
        images = images.astype(np.float32)

        # Normalize to [-1, 1]
        images = normalize_input(images)
        images = torch.from_numpy(images)

        if cuda_available:
            images = images.cuda()

        # Add batch dim
        if len(images.shape) == 3:
            images = images.unsqueeze(0)

        # channel first
        images = images.permute(0, 3, 1, 2)

        return images


    @staticmethod
    def is_valid_file(fname):
        ext = fname.split('.')[-1]
        return ext in VALID_FORMATS

        
