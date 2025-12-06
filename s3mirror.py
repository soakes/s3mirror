#!/usr/bin/env python3
"""
S3 Bucket Mirror Script
Professional-grade bucket mirroring for S3-compatible storage systems.

Features:
- Command-line arguments for flexible configuration
- Config file support (YAML/JSON)
- Multiple output modes (silent/normal/debug)
- File logging with rotation
- Cron-friendly operation
- Detailed statistics and reporting
"""

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import boto3
import botocore
import urllib3
import yaml
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

# ==========================================
# DEFAULT CONFIGURATION
# ==========================================

DEFAULT_CONFIG = {
    "source": {
        "endpoint_url": "https://s3.source.example.com",
        "aws_access_key_id": "SOURCE_ACCESS_KEY",
        "aws_secret_access_key": "SOURCE_SECRET_KEY",
        "region_name": "us-east-1",
        "verify_ssl": False,
    },
    "destination": {
        "endpoint_url": "https://s3.destination.example.com",
        "aws_access_key_id": "DEST_ACCESS_KEY",
        "aws_secret_access_key": "DEST_SECRET_KEY",
        "region_name": "us-east-1",
        "verify_ssl": False,
    },
    "performance": {
        "max_workers": 20,
        "multipart_threshold": 8_388_608,  # 8MB
        "multipart_chunksize": 8_388_608,  # 8MB
        "max_concurrency": 10,
        "max_pool_connections": 50,
    },
    "sync": {
        "delete_extraneous": True,
        "exclude_buckets": [],
    },
}


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",
    }

    def format(self, record):
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            levelname = record.levelname
            if levelname in self.COLORS:
                record.levelname = (
                    f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
                )
        return super().format(record)


class S3Mirror:  # pylint: disable=too-many-instance-attributes
    """High-performance S3 bucket mirroring engine."""

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger

        perf = config["performance"]
        self.max_workers = perf["max_workers"]
        self.multipart_threshold = perf["multipart_threshold"]
        self.multipart_chunksize = perf["multipart_chunksize"]
        self.max_concurrency = perf["max_concurrency"]
        self.max_pool_connections = perf["max_pool_connections"]

        self.delete_extraneous = config["sync"]["delete_extraneous"]
        self.exclude_buckets = set(config["sync"]["exclude_buckets"])

        self.logger.debug("=" * 70)
        self.logger.debug("INITIALIZATION")
        self.logger.debug("=" * 70)
        self.logger.debug("Source endpoint: %s", config["source"]["endpoint_url"])
        self.logger.debug(
            "Source access key: %s", config["source"]["aws_access_key_id"]
        )
        self.logger.debug(
            "Destination endpoint: %s",
            config["destination"]["endpoint_url"],
        )
        self.logger.debug(
            "Destination access key: %s",
            config["destination"]["aws_access_key_id"],
        )
        self.logger.debug("Max workers: %d", self.max_workers)
        self.logger.debug(
            "Multipart threshold: %s", self._format_bytes(self.multipart_threshold)
        )
        self.logger.debug(
            "Multipart chunk size: %s", self._format_bytes(self.multipart_chunksize)
        )
        self.logger.debug("Max concurrency: %d", self.max_concurrency)
        self.logger.debug("Connection pool: %d", self.max_pool_connections)
        self.logger.debug("Delete extraneous: %s", self.delete_extraneous)
        self.logger.debug("")

        self.source_client = self._create_client(config["source"], "SOURCE")
        self.dest_client = self._create_client(config["destination"], "DESTINATION")

        self.stats = {
            "buckets_processed": 0,
            "buckets_created": 0,
            "buckets_skipped": 0,
            "objects_copied": 0,
            "objects_deleted": 0,
            "bytes_transferred": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    def _create_client(self, endpoint_config: dict, label: str):
        boto_config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            max_pool_connections=self.max_pool_connections,
            retries={"max_attempts": 3, "mode": "adaptive"},
            tcp_keepalive=True,
        )

        self.logger.debug("Creating %s client...", label)

        return boto3.client(
            "s3",
            endpoint_url=endpoint_config["endpoint_url"],
            aws_access_key_id=endpoint_config["aws_access_key_id"],
            aws_secret_access_key=endpoint_config["aws_secret_access_key"],
            region_name=endpoint_config["region_name"],
            verify=endpoint_config.get("verify_ssl", False),
            config=boto_config,
        )

    def verify_connections(self) -> bool:
        """Test connectivity to both endpoints."""
        self.logger.debug("=" * 70)
        self.logger.debug("CONNECTION VERIFICATION")
        self.logger.debug("=" * 70)

        # Test source
        self.logger.debug("Testing SOURCE: %s", self.config["source"]["endpoint_url"])
        try:
            response = self.source_client.list_buckets()
            bucket_count = len(response["Buckets"])
            self.logger.info(
                "✓ Source connected successfully (%d buckets)", bucket_count
            )
            self.logger.debug(
                "  Response metadata: %s",
                response["ResponseMetadata"]["HTTPStatusCode"],
            )
            if bucket_count > 0:
                bucket_names = [b["Name"] for b in response["Buckets"][:5]]
                self.logger.debug("  Sample buckets: %s", ", ".join(bucket_names))
                if bucket_count > 5:
                    self.logger.debug("  ... and %d more", bucket_count - 5)
        except botocore.exceptions.ClientError as err:
            self.logger.error("✗ Source connection FAILED")
            self.logger.error("  Error code: %s", err.response["Error"]["Code"])
            self.logger.error("  Error message: %s", err.response["Error"]["Message"])
            self.logger.error(
                "  HTTP status: %s",
                err.response["ResponseMetadata"]["HTTPStatusCode"],
            )
            return False
        except Exception as err:  # pylint: disable=broad-except
            self.logger.error("✗ Source connection error: %s", err)
            return False

        # Test destination
        self.logger.debug(
            "Testing DESTINATION: %s", self.config["destination"]["endpoint_url"]
        )
        try:
            response = self.dest_client.list_buckets()
            bucket_count = len(response["Buckets"])
            self.logger.info(
                "✓ Destination connected successfully (%d buckets)", bucket_count
            )
            self.logger.debug(
                "  Response metadata: %s",
                response["ResponseMetadata"]["HTTPStatusCode"],
            )
        except botocore.exceptions.ClientError as err:
            self.logger.error("✗ Destination connection FAILED")
            self.logger.error("  Error code: %s", err.response["Error"]["Code"])
            self.logger.error("  Error message: %s", err.response["Error"]["Message"])
            self.logger.error(
                "  HTTP status: %s",
                err.response["ResponseMetadata"]["HTTPStatusCode"],
            )
            return False
        except Exception as err:  # pylint: disable=broad-except
            self.logger.error("✗ Destination connection error: %s", err)
            return False

        self.logger.debug("")
        return True

    def get_source_buckets(self) -> List[str]:
        """Retrieve list of bucket names from source."""
        try:
            response = self.source_client.list_buckets()
            all_buckets = [b["Name"] for b in response["Buckets"]]

            buckets = [b for b in all_buckets if b not in self.exclude_buckets]

            if self.exclude_buckets:
                excluded = [b for b in all_buckets if b in self.exclude_buckets]
                if excluded:
                    self.logger.info(
                        "Excluding %d bucket(s): %s", len(excluded), ", ".join(excluded)
                    )
                    self.logger.debug("  Excluded buckets: %s", excluded)

            self.logger.debug("Total buckets to sync: %d", len(buckets))
            return buckets
        except botocore.exceptions.ClientError as err:
            self.logger.error("Failed to list source buckets: %s", err)
            return []

    def bucket_exists(self, bucket_name: str) -> bool:
        """Check if bucket exists on destination."""
        try:
            self.dest_client.head_bucket(Bucket=bucket_name)
            return True
        except botocore.exceptions.ClientError:
            return False

    def create_bucket(self, bucket_name: str) -> bool:
        """Create bucket on destination if missing."""
        if self.bucket_exists(bucket_name):
            self.logger.debug("  Bucket already exists on destination")
            return True

        try:
            self.logger.debug("  Creating bucket on destination...")
            self.dest_client.create_bucket(Bucket=bucket_name)
            self.logger.info("  ✓ Created bucket: %s", bucket_name)
            self.stats["buckets_created"] += 1
            return True
        except botocore.exceptions.ClientError as err:
            self.logger.error("  ✗ Failed to create bucket: %s", err)
            self.logger.debug("    Error details: %s", err.response)
            self.stats["errors"] += 1
            return False

    def list_objects(self, client, bucket_name: str, label: str) -> Dict[str, int]:
        """List all objects in bucket, returning {key: size} mapping."""
        objects: Dict[str, int] = {}
        try:
            self.logger.debug("  Listing %s objects...", label)
            paginator = client.get_paginator("list_objects_v2")
            page_count = 0
            obj_count = 0

            for page in paginator.paginate(Bucket=bucket_name):
                page_count += 1
                if "Contents" in page:
                    for obj in page["Contents"]:
                        objects[obj["Key"]] = obj["Size"]
                        obj_count += 1

            self.logger.debug(
                "    Found %d objects across %d page(s)", obj_count, page_count
            )

            if obj_count > 0:
                sizes = list(objects.values())
                total_size = sum(sizes)
                avg_size = total_size / len(sizes)
                self.logger.debug("    Total size: %s", self._format_bytes(total_size))
                self.logger.debug(
                    "    Average size: %s", self._format_bytes(int(avg_size))
                )
                self.logger.debug("    Largest: %s", self._format_bytes(max(sizes)))
                self.logger.debug("    Smallest: %s", self._format_bytes(min(sizes)))

        except botocore.exceptions.ClientError as err:
            self.logger.error("  Failed to list objects in %s: %s", bucket_name, err)

        return objects

    def copy_object(self, bucket: str, key: str, size: int) -> bool:
        """Transfer single object from source to destination."""
        try:
            response = self.source_client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]

            transfer_config = TransferConfig(
                multipart_threshold=self.multipart_threshold,
                multipart_chunksize=self.multipart_chunksize,
                max_concurrency=self.max_concurrency,
                use_threads=True,
                max_io_queue=1000,
            )

            self.dest_client.upload_fileobj(body, bucket, key, Config=transfer_config)
            body.close()

            self.stats["bytes_transferred"] += size

            size_str = self._format_bytes(size)
            if size >= self.multipart_threshold:
                self.logger.debug("    ✓ %s [%s] (multipart)", key, size_str)
            else:
                self.logger.debug("    ✓ %s [%s]", key, size_str)

            return True
        except Exception as err:  # pylint: disable=broad-except
            self.logger.error("    ✗ Failed to copy %s: %s", key, err)
            return False

    def delete_object(self, bucket: str, key: str) -> bool:
        """Remove object from destination."""
        try:
            self.dest_client.delete_object(Bucket=bucket, Key=key)
            self.logger.debug("    ✓ Deleted: %s", key)
            return True
        except botocore.exceptions.ClientError as err:
            self.logger.error("    ✗ Failed to delete %s: %s", key, err)
            return False

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        """Convert bytes to human-readable format."""
        value = float(num_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024.0:
                return f"{value:.1f}{unit}"
            value /= 1024.0
        return f"{value:.1f}PB"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Convert seconds to readable duration."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        if seconds < 3600:
            return f"{seconds/60:.1f}m"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"

    def _calculate_differences(
        self,
        src_objects: Dict[str, int],
        dest_objects: Dict[str, int],
    ) -> Tuple[List[Tuple[str, int]], Set[str], int]:
        """Determine what to copy and delete, and total bytes to copy."""
        to_copy: List[Tuple[str, int]] = []
        to_delete: Set[str] = set()
        total_copy_bytes = 0

        for key, size in src_objects.items():
            if key not in dest_objects:
                to_copy.append((key, size))
                total_copy_bytes += size
                self.logger.debug(
                    "    New file: %s (%s)", key, self._format_bytes(size)
                )
            elif dest_objects[key] != size:
                to_copy.append((key, size))
                total_copy_bytes += size
                self.logger.debug(
                    "    Changed: %s (src:%s != dst:%s)",
                    key,
                    self._format_bytes(size),
                    self._format_bytes(dest_objects[key]),
                )

        if self.delete_extraneous:
            extra = set(dest_objects.keys()) - set(src_objects.keys())
            if extra:
                to_delete = extra
                self.logger.debug("  Files to delete: %d", len(to_delete))
                for k in list(to_delete)[:5]:
                    self.logger.debug("    - %s", k)
                if len(to_delete) > 5:
                    self.logger.debug("    ... and %d more", len(to_delete) - 5)

        return to_copy, to_delete, total_copy_bytes

    def _run_parallel_copy(
        self,
        bucket_name: str,
        to_copy: List[Tuple[str, int]],
        total_copy_bytes: int,
    ) -> Tuple[int, int]:
        """Execute parallel object copies and return (success, failed)."""
        if not to_copy:
            return 0, 0

        self.logger.debug("  Starting parallel copy (%d workers)...", self.max_workers)
        copy_start = time.time()
        success = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.copy_object, bucket_name, key, size): (key, size)
                for key, size in to_copy
            }

            completed = 0
            for future in as_completed(futures):
                completed += 1
                if future.result():
                    success += 1
                    self.stats["objects_copied"] += 1
                else:
                    failed += 1
                    self.stats["errors"] += 1

                if completed % 50 == 0:
                    progress_pct = (completed / len(to_copy)) * 100
                    self.logger.debug(
                        "  Progress: %d/%d (%.0f%%) - %d ok, %d failed",
                        completed,
                        len(to_copy),
                        progress_pct,
                        success,
                        failed,
                    )

        copy_duration = time.time() - copy_start
        throughput = total_copy_bytes / copy_duration if copy_duration > 0 else 0.0
        self.logger.info(
            "  ✓ Copied %d objects in %.1fs (%s/s)",
            success,
            copy_duration,
            self._format_bytes(int(throughput)),
        )
        if failed > 0:
            self.logger.warning("  ⚠ %d objects failed to copy", failed)

        return success, failed

    def sync_bucket(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, bucket_name: str, bucket_num: int, total_buckets: int
    ) -> None:
        """Synchronize single bucket from source to destination."""
        self.logger.info("")
        self.logger.info("=" * 70)
        self.logger.info("[%d/%d]: %s", bucket_num, total_buckets, bucket_name)
        self.logger.info("=" * 70)

        bucket_start = time.time()

        self.logger.debug("  Checking if bucket exists on destination...")
        if not self.create_bucket(bucket_name):
            self.logger.warning("  Skipping bucket due to creation failure")
            self.stats["buckets_skipped"] += 1
            return

        src_objects = self.list_objects(self.source_client, bucket_name, "source")
        dest_objects = self.list_objects(self.dest_client, bucket_name, "destination")

        self.logger.info("  Source: %d objects", len(src_objects))
        self.logger.info("  Destination: %d objects", len(dest_objects))

        # Calculate differences
        self.logger.debug("  Calculating differences...")
        to_copy, to_delete, total_copy_bytes = self._calculate_differences(
            src_objects, dest_objects
        )

        if not to_copy and not to_delete:
            self.logger.info("  ✓ Already synchronized (no changes needed)")
            self.stats["buckets_processed"] += 1
            bucket_duration = time.time() - bucket_start
            self.logger.debug("  Bucket processing time: %.1fs", bucket_duration)
            return

        self.logger.info(
            "  Actions: %d to copy (%s), %d to delete",
            len(to_copy),
            self._format_bytes(total_copy_bytes),
            len(to_delete),
        )

        success_copies, _failed_copies = self._run_parallel_copy(
            bucket_name, to_copy, total_copy_bytes
        )

        if to_delete:
            self.logger.debug(
                "  Starting parallel delete (%d workers)...", self.max_workers
            )
            delete_start = time.time()
            success_del = 0
            failed_del = 0

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self.delete_object, bucket_name, key): key
                    for key in to_delete
                }
                for future in as_completed(futures):
                    if future.result():
                        success_del += 1
                        self.stats["objects_deleted"] += 1
                    else:
                        failed_del += 1
                        self.stats["errors"] += 1

            delete_duration = time.time() - delete_start
            self.logger.info(
                "  ✓ Deleted %d objects in %.1fs", success_del, delete_duration
            )
            if failed_del > 0:
                self.logger.warning("  ⚠ %d objects failed to delete", failed_del)

        bucket_duration = time.time() - bucket_start
        self.logger.info("  ✓ Bucket completed in %.1fs", bucket_duration)
        self.logger.debug(
            "  Bucket stats: copied=%d, deleted=%d, errors=%d",
            success_copies,
            self.stats["objects_deleted"],
            self.stats["errors"],
        )
        self.stats["buckets_processed"] += 1

    def mirror_all_buckets(self) -> None:
        """Execute full mirror operation across all buckets."""
        self.logger.debug("=" * 70)
        self.logger.debug("BUCKET DISCOVERY")
        self.logger.debug("=" * 70)

        buckets = self.get_source_buckets()

        if not buckets:
            self.logger.warning("No buckets found to sync")
            return

        self.logger.info("")
        self.logger.info("Starting synchronization of %d bucket(s)", len(buckets))
        self.logger.info(
            "Performance: %d workers, %s multipart threshold",
            self.max_workers,
            self._format_bytes(self.multipart_threshold),
        )

        for idx, bucket in enumerate(buckets, 1):
            self.sync_bucket(bucket, idx, len(buckets))

    def print_summary(self) -> None:
        """Generate and log final summary report."""
        duration = time.time() - self.stats["start_time"]

        self.logger.info("")
        self.logger.info("=" * 70)
        self.logger.info("FINAL SUMMARY")
        self.logger.info("=" * 70)
        self.logger.info(
            "Started:  %s",
            datetime.fromtimestamp(self.stats["start_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        self.logger.info("Finished: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.logger.info(
            "Duration: %.1fs (%s)", duration, self._format_duration(duration)
        )
        self.logger.info("")
        self.logger.info("Buckets:")
        self.logger.info("  - Processed: %d", self.stats["buckets_processed"])
        self.logger.info("  - Created:   %d", self.stats["buckets_created"])
        if self.stats["buckets_skipped"] > 0:
            self.logger.info("  - Skipped:   %d", self.stats["buckets_skipped"])
        self.logger.info("")
        self.logger.info("Objects:")
        self.logger.info("  - Copied:    %d", self.stats["objects_copied"])
        self.logger.info("  - Deleted:   %d", self.stats["objects_deleted"])
        self.logger.info("")
        self.logger.info(
            "Data transferred: %s",
            self._format_bytes(self.stats["bytes_transferred"]),
        )

        if duration > 0 and self.stats["bytes_transferred"] > 0:
            throughput = self.stats["bytes_transferred"] / duration
            self.logger.info(
                "Average throughput: %s/s",
                self._format_bytes(int(throughput)),
            )

        self.logger.info("")
        if self.stats["errors"] > 0:
            self.logger.warning("⚠ Errors encountered: %d", self.stats["errors"])
            self.logger.info("Status: COMPLETED WITH ERRORS")
        else:
            self.logger.info("✓ Status: COMPLETED SUCCESSFULLY")

        self.logger.info("=" * 70)


def setup_logging(args: argparse.Namespace) -> logging.Logger:
    """Configure logging with appropriate handlers and formatters."""
    logger = logging.getLogger("s3mirror")
    logger.setLevel(logging.DEBUG)

    logger.handlers.clear()

    if args.log_file and not args.debug:
        console_level = logging.ERROR
    elif args.quiet:
        console_level = logging.ERROR
    elif args.debug:
        console_level = logging.DEBUG
    else:
        console_level = logging.INFO

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)

    if args.debug:
        console_format = ColoredFormatter("[%(levelname)s] %(message)s")
    else:
        console_format = logging.Formatter("%(message)s")

    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    if args.log_file:
        file_handler = logging.FileHandler(args.log_file, mode="a")
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

        logger.info("")
        logger.info("#" * 70)
        logger.info("# NEW SESSION: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("#" * 70)

    return logger


def load_config(config_path: Optional[str]) -> dict:
    """Load configuration from file or use defaults."""
    if not config_path:
        return DEFAULT_CONFIG.copy()

    config_file = Path(config_path)

    if not config_file.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(config_file, "r", encoding="utf-8") as file:
            if config_path.endswith(".json"):
                user_config = json.load(file)
            elif config_path.endswith(".yaml") or config_path.endswith(".yml"):
                user_config = yaml.safe_load(file)
            else:
                print(
                    "Error: Unsupported config format. Use .json or .yaml",
                    file=sys.stderr,
                )
                sys.exit(1)

        config = DEFAULT_CONFIG.copy()
        for key in user_config:
            if isinstance(user_config[key], dict):
                config[key].update(user_config[key])
            else:
                config[key] = user_config[key]

        return config

    except Exception as err:  # pylint: disable=broad-except
        print(f"Error loading config file: {err}", file=sys.stderr)
        sys.exit(1)


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="S3 Bucket Mirror - Professional bucket synchronization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal operation (console output)
  %(prog)s

  # Silent mode for cron
  %(prog)s --quiet

  # Log to file (console is quiet, all output to file)
  %(prog)s --log-file /var/log/s3mirror.log

  # Log to file with debug console output too
  %(prog)s --debug --log-file /var/log/s3mirror.log

  # Use custom config file
  %(prog)s --config /etc/s3mirror.yaml --log-file /var/log/s3mirror.log
        """,
    )

    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        help="Configuration file path (.json or .yaml)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode - only errors (for cron)",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Debug mode - verbose output",
    )
    parser.add_argument(
        "-l",
        "--log-file",
        metavar="FILE",
        help="Log to file (console quiet unless --debug specified)",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        metavar="N",
        help="Number of parallel workers (default: 20)",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Don't delete extraneous files from destination",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Display configuration and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="S3 Mirror v1.0.0",
    )

    return parser.parse_args()


def main():
    """Main application entry point."""
    args = parse_arguments()

    config = load_config(args.config)

    if args.workers:
        config["performance"]["max_workers"] = args.workers

    if args.no_delete:
        config["sync"]["delete_extraneous"] = False

    if args.show_config:
        display_config = json.loads(json.dumps(config))
        display_config["source"]["aws_secret_access_key"] = "***REDACTED***"
        display_config["destination"]["aws_secret_access_key"] = "***REDACTED***"
        print(json.dumps(display_config, indent=2))
        sys.exit(0)

    logger = setup_logging(args)

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    logger.info("=" * 70)
    logger.info("S3 BUCKET MIRROR - Production Edition v1.0.0")
    logger.info("Session started: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 70)

    try:
        mirror = S3Mirror(config, logger)

        if not mirror.verify_connections():
            logger.error("Connection verification failed - aborting")
            sys.exit(1)

        mirror.mirror_all_buckets()
        mirror.print_summary()

        sys.exit(1 if mirror.stats["errors"] > 0 else 0)

    except KeyboardInterrupt:
        logger.warning("\n\nOperation cancelled by user (Ctrl+C)")
        sys.exit(130)
    except Exception as err:  # pylint: disable=broad-except
        logger.exception("FATAL ERROR: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
