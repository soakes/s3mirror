# S3 Mirror 🪞

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Lint Status](https://github.com/soakes/s3mirror/actions/workflows/lint.yml/badge.svg)](https://github.com/soakes/s3mirror/actions/workflows/lint.yml)
[![Format Status](https://github.com/soakes/s3mirror/actions/workflows/format.yml/badge.svg)](https://github.com/soakes/s3mirror/actions/workflows/format.yml)
[![GitHub Issues](https://img.shields.io/github/issues/soakes/s3mirror)](https://github.com/soakes/s3mirror/issues)

**S3 Mirror** is a production-ready Python utility for synchronizing buckets and objects between S3-compatible endpoints. Built on `boto3`, it provides enterprise-grade reliability with comprehensive logging, parallelized transfers, and automation-friendly operation.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Logging](#logging)
- [Safety Considerations](#safety-considerations)
- [Continuous Integration](#continuous-integration)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

**Motivation**: While MinIO's `mc` client has served as a capable mirroring tool, recent upstream changes and deprecation of essential features created concerns about long-term reliability and availability. S3 Mirror addresses this gap by providing:

- **Complete independence** from proprietary tooling ecosystems
- **Foundation on boto3**, the industry-standard AWS SDK for Python
- **Full transparency and auditability** of synchronization operations
- **Universal S3 compatibility** across AWS, MinIO, Ceph, Backblaze B2, Wasabi, and other providers

This tool is designed for infrastructure engineers and DevOps teams requiring dependable, scriptable S3 replication without vendor lock-in.

---

## Key Features

✅ **Multi-Endpoint Synchronization** – Mirror buckets and objects between any S3-compatible services  
✅ **Performance Optimization** – Configurable parallelization with multipart upload support  
✅ **True Mirroring** – Optional deletion of extraneous destination objects for exact replication  
✅ **Flexible Configuration** – YAML/JSON config files with CLI flag overrides  
✅ **Production Logging** – Multiple logging modes including cron-friendly file output with silent console operation  
✅ **Automation Ready** – Idempotent design for reliable scheduled execution  
✅ **CI/CD Validated** – Automated linting and formatting across Python 3.10–3.13  
✅ **Dependency Management** – Automated security updates via Dependabot  

---

## Prerequisites

- **Python 3.10 or higher** (tested through 3.13)
- **S3 Credentials**: AWS access keys or IAM credentials for both source and destination endpoints
- **Network Access**: Connectivity to both S3 endpoints (including proxy/firewall configuration if required)

---

## Installation

Clone the repository and set up the Python environment:

```bash
git clone https://github.com/soakes/s3mirror.git
cd s3mirror

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

S3 Mirror uses YAML or JSON configuration files to define connection parameters and synchronization behavior. Create a config file based on the template below:

```yaml
source:
  endpoint_url: "https://s3.source.example.com"
  aws_access_key_id: "SOURCE_ACCESS_KEY"
  aws_secret_access_key: "SOURCE_SECRET_KEY"
  region_name: "us-east-1"
  verify_ssl: false

destination:
  endpoint_url: "https://s3.destination.example.com"
  aws_access_key_id: "DEST_ACCESS_KEY"
  aws_secret_access_key: "DEST_SECRET_KEY"
  region_name: "us-east-1"
  verify_ssl: false

performance:
  max_workers: 20                # Parallel transfer threads
  multipart_threshold: 8388608   # 8 MB - files larger trigger multipart upload
  multipart_chunksize: 8388608   # 8 MB - chunk size for multipart uploads
  max_concurrency: 10            # Concurrent S3 operations per thread
  max_pool_connections: 50       # HTTP connection pool size

sync:
  delete_extraneous: true        # Remove objects in destination not present in source
  exclude_buckets: []            # Bucket names to skip during mirroring
```

### Configuration Parameters

**Source/Destination Blocks**:
- `endpoint_url`: S3-compatible API endpoint URL
- `aws_access_key_id` / `aws_secret_access_key`: Authentication credentials
- `region_name`: AWS region identifier (required even for non-AWS endpoints)
- `verify_ssl`: SSL certificate verification (disable for self-signed certificates)

**Performance Tuning**:
- Adjust `max_workers` based on available CPU cores and network bandwidth
- Set `multipart_threshold` and `multipart_chunksize` according to typical object sizes
- Increase `max_pool_connections` for high-throughput scenarios

**Sync Behavior**:
- `delete_extraneous`: Enable true mirroring by removing destination-only objects
- `exclude_buckets`: Skip specific buckets (useful for test/temporary buckets)

---

## Usage

### Basic Operation

Execute a synchronization using your configuration file:

```bash
./s3mirror.py --config config.yaml
```

### Command-Line Options

```bash
# Silent mode (console shows errors only)
./s3mirror.py --config config.yaml --quiet

# Log to file with silent console (ideal for cron jobs)
./s3mirror.py --config config.yaml --log-file /var/log/s3mirror.log

# Debug mode with verbose output
./s3mirror.py --config config.yaml --debug

# Disable deletion of extraneous objects
./s3mirror.py --config config.yaml --no-delete
```

### Cron Automation Example

Add to your crontab for scheduled synchronization:

```bash
# Run daily at 2:00 AM with file logging
0 2 * * * /path/to/s3mirror/.venv/bin/python /path/to/s3mirror/s3mirror.py --config /path/to/config.yaml --log-file /var/log/s3mirror.log --quiet
```

---

## Logging

S3 Mirror provides multiple logging modes tailored to different operational contexts:

| Mode | Console Output | File Output | Use Case |
|------|----------------|-------------|----------|
| **Normal** | Human-readable progress messages | None | Interactive execution |
| **Debug** | Colorized `[LEVEL]` messages with details | None | Troubleshooting |
| **File Log** | Errors only | Full DEBUG with timestamps | Production automation |
| **Quiet** | None (unless errors occur) | None | Minimal output scenarios |

**Recommendation**: For production cron jobs, use `--log-file` with `--quiet` to maintain detailed logs while preventing unnecessary console output.

---

## Safety Considerations

⚠️ **Deletion Behavior**: When `delete_extraneous: true`, S3 Mirror removes objects from the destination that do not exist in the source. This ensures perfect replication but requires careful consideration.

**Best Practices**:
- **Test in non-production environments first** to validate configuration
- **Enable deletion only when true mirroring is required** (vs. one-way copying)
- **Use `exclude_buckets`** to protect specific buckets from synchronization
- **Review logs regularly** to identify unexpected deletions or errors
- **Maintain backup copies** of critical data before enabling deletion

To disable deletion while still copying new/changed objects:
```bash
./s3mirror.py --config config.yaml --no-delete
```

Or set `delete_extraneous: false` in the configuration file.

---

## Continuous Integration

[![Lint Status](https://github.com/soakes/s3mirror/actions/workflows/lint.yml/badge.svg)](https://github.com/soakes/s3mirror/actions/workflows/lint.yml)
[![Format Status](https://github.com/soakes/s3mirror/actions/workflows/format.yml/badge.svg)](https://github.com/soakes/s3mirror/actions/workflows/format.yml)

Every commit and pull request is automatically validated through GitHub Actions across **Python 3.10 through 3.13**:

### Code Quality Workflows

**Linting** (`lint.yml`):
- **Pylint**: Static code analysis for code quality and standards compliance
- **Cross-version testing**: Validates compatibility across all supported Python versions

**Formatting** (`format.yml`):
- **Black**: Code formatting verification (PEP 8 conformance)
- **isort**: Import statement organization
- **Consistent style enforcement** across the entire codebase

### Dependency Management

**Dependabot** (`dependabot.yml`):
- **Automated dependency updates** for security patches and version bumps
- **Weekly scanning** of Python packages and GitHub Actions
- **Auto-merge workflow** (`dependabot-auto-merge.yml`) for patch and minor updates

The CI pipeline ensures code quality, security, and cross-version compatibility, providing confidence for production deployment.

---

## Project Structure

```
s3mirror/
├── .github/
│   ├── dependabot.yml                    # Dependabot configuration
│   └── workflows/
│       ├── dependabot-auto-merge.yml     # Auto-merge for dependency updates
│       ├── format.yml                    # Code formatting checks (Black, isort)
│       └── lint.yml                      # Linting workflow (Pylint)
├── .pylintrc                             # Pylint configuration and standards
├── LICENSE                               # MIT License
├── README.md                             # This documentation
├── requirements.txt                      # Python dependencies (boto3, PyYAML, etc.)
└── s3mirror.py                           # Main synchronization script
```

---

## Contributing

Contributions are welcome and appreciated. To contribute:

1. **Fork the repository** on GitHub
2. **Create a feature branch** (`git checkout -b feature/your-feature`)
3. **Implement your changes** with appropriate tests
4. **Ensure CI passes** (run `pylint`, `black`, and `isort` locally)
5. **Submit a pull request** with a clear description of changes

### Local Development

Run code quality checks before committing:

```bash
# Format code
black s3mirror.py

# Sort imports
isort s3mirror.py

# Run linter
pylint s3mirror.py
```

**Areas for contribution**:
- Bug fixes and reliability improvements
- Performance optimizations
- Enhanced error handling and recovery
- Documentation improvements
- Additional S3-compatible endpoint testing

Please open an issue before starting work on major features to discuss implementation approach.

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for complete details.

---

**Developed by Simon Oakes**  
*Infrastructure Engineer | Open Source Contributor*  
© 2025

---
