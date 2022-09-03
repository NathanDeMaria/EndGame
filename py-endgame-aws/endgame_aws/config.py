import json
from dataclasses import dataclass
from pathlib import Path


_PROJECT_ROOT = Path(__file__).parent.parent


@dataclass
class Config:
    """
    A config class for AWS account-specific settings
    """

    bucket: str

    @classmethod
    def init_from_file(cls):
        """
        Initialize the file from config, assuming you've put the config
        created by running `make outputs` in my AWS batch repo:

        https://github.com/NathanDeMaria/aws-batch-optimization/blob/7ff0b5c37f4f7fd00cbf758ec78baf019d418ab0/infra/Makefile#L7
        """
        with open(_PROJECT_ROOT / "config.json", encoding="utf-8") as file:
            raw = json.load(file)
        return cls(bucket=raw["bucket"]["value"])
