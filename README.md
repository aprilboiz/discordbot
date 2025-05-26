# DiscordMusicBot

Simple Discord Music Bot can play songs from YouTube or SoundCloud.

## Features
- Convert text to speech
- Play music from YouTube or SoundCloud
- Calculate sleep time

More features will be added later...

## How to use?
This bot is mainly written in Python. So you will need to install Python before doing other things.

### Prerequisites
- [NodeJS](https://nodejs.org/en)
- [Python](https://www.python.org/) >= 3.12
- [FFmpeg](https://ffmpeg.org/)
- Required packages are listed in `pyproject.toml`.
- You will need a token for your bot to run. This token can be taken at Discord Developer Portal. Place your token in the `.env` file in the root folder with the format `TOKEN=<your_token>`

## Installation
### Install dependencies
#### If you are using UV as your project manager. Open your terminal and type this command:
```
uv sync --locked
```
#### For Python. Open your terminal and type this command:
```
pip install .
```

### Running Locally
After doing these things, your bot now can be run. Open your terminal in the root folder and type this command:
```sh
python main.py
```
#### If you are using UV: 
```
uv run main.py
```
By now, your bot should be running properly. If it does not run, take a quick look in the `bot.log` and `error.log` files for errors.

### Running with Docker
You can also run the bot using Docker. First, build the Docker image:
```sh
docker build -t discord-music-bot .
```
Then, run the Docker container:
```sh
docker run -it -v /path/to/logs:/app/logs --env-file .env -d --name discord-music-bot discord-music-bot
```
This will start the bot in a Docker container. Make sure to replace `/path/to/logs` with the actual path where you want to store the logs.