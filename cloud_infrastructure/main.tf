terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "4.3.0"
    }
  }
}

provider "aws" {
  profile = "default"
  region  = "us-east-2"
}

resource "aws_s3_bucket" "cache-bucket" {
  bucket = "endgame-cache"
  tags   = {}
}
