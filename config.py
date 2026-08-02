import os
import platform
from pathlib import Path
import json

class HyCLIP_Config:
	DEFAULT_CONFIG = {
		"API_URL": "http://localhost:45869",
		"API_KEY": None,
		"HYCLIP_API_URL": "http://localhost:8000",
		"TAG_SERVICE_KEY": None,
		"RATING_SERVICE_KEY": None,
		"VECTOR_QUANT": "UINT8",
		"CLIP_MODEL": "ViT-B-16-SigLIP2",
		"LOAD_MODEL_ON_STARTUP": False,
		"THUMB_SIZE": 200,
		"SEARCH_LIMIT": 100,
		"BUCKET_CACHE_TIMEOUT": 300
	}

	def __init__(self):
		self.load_config()

	# Platform-agnostic config location:
	#   Windows: %APPDATA%/hyclip/config.json
	#   macOS:   ~/Library/Application Support/hyclip/config.json
	#   Linux:   $XDG_CONFIG_HOME/hyclip/config.json (default ~/.config)
	def _config_path(self) -> Path:
		system = platform.system()
		if system == "Windows":
			base = Path(os.environ.get("APPDATA", Path.home()))
		elif system == "Darwin":
			base = Path.home() / "Library" / "Application Support"
		else:
			base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

		return base / "hyclip" / "config.json"

	def load_config(self):
		self.config_path = self._config_path()

		try:
			with open(self.config_path) as F:
				cfg = json.load(F)
		except FileNotFoundError:
			cfg = {}

		# File overrides defaults; anything missing falls back to the default
		self.cfg = {**self.DEFAULT_CONFIG, **cfg}

		for key, value in self.cfg.items():
			setattr(self, key, value)

	def save_config(self):
		for key in self.cfg:
			self.cfg[key] = getattr(self, key)

		self.config_path.parent.mkdir(parents=True, exist_ok=True)

		with open(self.config_path, "w") as F:
			json.dump(self.cfg, F, indent=4)


if __name__ == "__main__":
	# Used by run.sh/ingest.sh: prints "<url> <host> <port>" for uvicorn to bind to.
	import urllib.parse as u

	c = HyCLIP_Config()
	url = os.environ.get("HYCLIP_API_URL") or c.HYCLIP_API_URL
	p = u.urlparse(url)
	print(url, p.hostname or "127.0.0.1", p.port or 80)
