# Requirements Document: Clanker v4.0.0 SDK Integration

## Introduction

This document specifies the requirements for migrating from Clanker v3.1.0 contract-based deployment to v4.0.0 SDK-based deployment. The Clanker v4.0.0 SDK is a TypeScript/Node.js SDK that uses viem for blockchain interactions. The integration will use a Node.js wrapper script approach, allowing the Python application to invoke the TypeScript SDK via subprocess while maintaining clean separation of concerns and error handling.

## Glossary

- **ClankerDeployer**: Python class responsible for orchestrating token deployment using Clanker SDK v4.0.0
- **DeployRequest**: Data structure containing all information needed to deploy a token
- **DeployResult**: Data structure containing the outcome of a deployment attempt
- **Node_Script**: JavaScript wrapper that bridges Python and TypeScript Clanker SDK
- **SDK**: Clanker v4.0.0 TypeScript/Node.js software development kit
- **Base_Chain**: Base blockchain network (Layer 2 on Ethereum)
- **WETH**: Wrapped Ether token on Base network
- **RPC_Endpoint**: Remote procedure call endpoint for blockchain interaction
- **Subprocess**: Child process spawned by Python to execute Node.js script
- **IPFS_URI**: InterPlanetary File System uniform resource identifier (ipfs://...)
- **EVM_Address**: Ethereum Virtual Machine address (0x + 40 hex characters)
- **Tax_BPS**: Tax rate in basis points (1 bps = 0.01%, 10000 bps = 100%)

## Requirements

### Requirement 1: SDK Configuration Building

**User Story:** As a deployment system, I want to build valid v4.0.0 SDK configuration from deployment requests, so that tokens can be deployed with correct parameters.

#### Acceptance Criteria

1. WHEN a valid DeployRequest is provided, THE ClankerDeployer SHALL create a configuration dictionary containing all required v4.0.0 SDK fields
2. THE configuration SHALL include name, symbol, tokenAdmin, image, metadata, context, pool, fees, rewards, vault, and devBuy fields
3. THE configuration name field SHALL equal the DeployRequest token_name
4. THE configuration symbol field SHALL equal the DeployRequest token_symbol
5. THE configuration tokenAdmin field SHALL equal the DeployRequest token_admin
6. THE configuration image field SHALL equal the DeployRequest image_uri
7. THE configuration pool pairedToken field SHALL equal the WETH address on Base (0x4200000000000000000000000000000000000006)
8. THE configuration fees clankerFee field SHALL equal the DeployRequest tax_bps
9. THE configuration rewards recipients SHALL contain exactly one recipient with bps equal to 10000
10. THE configuration vault field SHALL be null
11. THE configuration devBuy field SHALL be null

### Requirement 2: Configuration Validation

**User Story:** As a deployment system, I want to validate configuration before deployment, so that invalid deployments are prevented early.

#### Acceptance Criteria

1. WHEN preflight validation is performed, THE ClankerDeployer SHALL verify that token_name is non-empty and maximum 50 characters
2. WHEN preflight validation is performed, THE ClankerDeployer SHALL verify that token_symbol is non-empty, maximum 10 characters, and uppercase
3. WHEN preflight validation is performed, THE ClankerDeployer SHALL verify that tokenAdmin is a valid EVM address
4. WHEN preflight validation is performed, THE ClankerDeployer SHALL verify that image is a valid IPFS URI
5. WHEN preflight validation is performed, THE ClankerDeployer SHALL verify that tax_bps is between 0 and 10000 inclusive
6. IF validation fails, THEN THE ClankerDeployer SHALL raise ValueError with descriptive error message

### Requirement 3: SDK Availability Check

**User Story:** As a deployment system, I want to verify SDK availability before deployment, so that missing dependencies are detected early.

#### Acceptance Criteria

1. WHEN checking SDK availability, THE ClankerDeployer SHALL verify that Node.js is installed and available in PATH
2. WHEN Node.js is not available, THE ClankerDeployer SHALL return False from availability check
3. WHEN Node.js is available, THE ClankerDeployer SHALL return True from availability check
4. WHEN Clanker SDK is not installed globally, THE ClankerDeployer SHALL log a warning message
5. THE availability check SHALL not raise exceptions

### Requirement 4: Subprocess Execution

**User Story:** As a deployment system, I want to execute Node.js wrapper script via subprocess, so that TypeScript SDK can be invoked from Python.

#### Acceptance Criteria

1. WHEN executing deployment, THE ClankerDeployer SHALL write configuration to a temporary JSON file
2. WHEN executing deployment, THE ClankerDeployer SHALL spawn subprocess with command ["node", script_path, config_file_path]
3. WHEN executing deployment, THE ClankerDeployer SHALL set subprocess timeout to 120 seconds
4. WHEN subprocess completes, THE ClankerDeployer SHALL capture stdout, stderr, and exit code
5. WHEN subprocess times out, THE ClankerDeployer SHALL kill the process
6. WHEN subprocess execution completes or fails, THE ClankerDeployer SHALL delete the temporary configuration file

### Requirement 5: Output Parsing

**User Story:** As a deployment system, I want to parse Node.js script output into structured results, so that deployment outcomes can be processed programmatically.

#### Acceptance Criteria

1. WHEN subprocess exit code is non-zero, THE ClankerDeployer SHALL return DeployResult with status "deploy_failed"
2. WHEN subprocess stdout contains valid JSON with status "success", THE ClankerDeployer SHALL extract txHash and contractAddress
3. WHEN subprocess stdout contains valid JSON with status "error", THE ClankerDeployer SHALL extract errorCode and errorMessage
4. WHEN subprocess stdout contains malformed JSON, THE ClankerDeployer SHALL return DeployResult with status "deploy_failed" and errorCode "parse_error"
5. WHEN parsing succeeds with status "success", THE ClankerDeployer SHALL return DeployResult with status "deploy_success", txHash, and contractAddress
6. WHEN parsing succeeds with status "error", THE ClankerDeployer SHALL return DeployResult with status "deploy_failed", errorCode, and errorMessage
7. THE output parsing SHALL never raise exceptions

### Requirement 6: Node.js Wrapper Script

**User Story:** As a deployment system, I want a Node.js script that bridges Python and TypeScript SDK, so that SDK functionality is accessible from Python.

#### Acceptance Criteria

1. WHEN Node_Script is executed, THE Node_Script SHALL read configuration from file path provided as command-line argument
2. WHEN Node_Script reads configuration, THE Node_Script SHALL parse it as JSON
3. WHEN Node_Script has valid configuration, THE Node_Script SHALL initialize Clanker SDK with environment variables
4. WHEN Node_Script initializes SDK, THE Node_Script SHALL execute deployment transaction
5. WHEN deployment succeeds, THE Node_Script SHALL output JSON to stdout with status "success", txHash, and contractAddress
6. WHEN deployment fails, THE Node_Script SHALL output JSON to stderr with status "error", errorCode, and errorMessage
7. WHEN deployment succeeds, THE Node_Script SHALL exit with code 0
8. WHEN deployment fails, THE Node_Script SHALL exit with non-zero code
9. THE Node_Script SHALL use DEPLOYER_SIGNER_PRIVATE_KEY environment variable for transaction signing
10. THE Node_Script SHALL use BASE_RPC_URL environment variable for blockchain connection

### Requirement 7: Error Handling

**User Story:** As a deployment system, I want comprehensive error handling, so that all failure modes are captured and reported clearly.

#### Acceptance Criteria

1. WHEN Node.js is not installed, THE ClankerDeployer SHALL return DeployResult with errorCode "sdk_not_available"
2. WHEN Clanker SDK is not installed, THE ClankerDeployer SHALL return DeployResult with errorCode "sdk_not_installed"
3. WHEN deployer wallet has insufficient ETH, THE ClankerDeployer SHALL return DeployResult with errorCode "insufficient_gas"
4. WHEN configuration validation fails, THE ClankerDeployer SHALL return DeployResult with errorCode "invalid_config"
5. WHEN subprocess times out, THE ClankerDeployer SHALL return DeployResult with errorCode "timeout"
6. WHEN JSON parsing fails, THE ClankerDeployer SHALL return DeployResult with errorCode "parse_error"
7. WHEN contract deployment reverts, THE ClankerDeployer SHALL return DeployResult with errorCode "contract_revert"
8. THE deploy method SHALL never raise exceptions to caller
9. WHEN errors occur, THE ClankerDeployer SHALL log error details for debugging

### Requirement 8: Security

**User Story:** As a deployment system, I want secure handling of sensitive data, so that private keys and credentials are protected.

#### Acceptance Criteria

1. THE ClankerDeployer SHALL read DEPLOYER_SIGNER_PRIVATE_KEY from environment variable only
2. THE ClankerDeployer SHALL never log private keys
3. THE ClankerDeployer SHALL never write private keys to disk
4. THE ClankerDeployer SHALL pass private key to Node_Script via environment variables, not command-line arguments
5. THE ClankerDeployer SHALL validate all EVM addresses to prevent injection attacks
6. THE ClankerDeployer SHALL validate IPFS URIs to ensure well-formed format
7. THE ClankerDeployer SHALL use absolute path for Node_Script to prevent path traversal
8. THE ClankerDeployer SHALL use HTTPS for RPC endpoint connections

### Requirement 9: Performance

**User Story:** As a deployment system, I want efficient deployment execution, so that tokens are deployed in reasonable time.

#### Acceptance Criteria

1. WHEN deployment succeeds, THE deployment SHALL complete within 60 seconds under normal conditions
2. THE subprocess timeout SHALL be set to 120 seconds maximum
3. THE temporary configuration file SHALL be less than 10 KB
4. THE ClankerDeployer SHALL support sequential deployments without memory leaks

### Requirement 10: Deployment Result Structure

**User Story:** As a deployment system, I want structured deployment results, so that outcomes can be processed and stored consistently.

#### Acceptance Criteria

1. WHEN deployment completes, THE ClankerDeployer SHALL return DeployResult object
2. THE DeployResult SHALL have status field with value "deploy_success" or "deploy_failed"
3. WHEN status is "deploy_success", THE DeployResult SHALL have non-null tx_hash field
4. WHEN status is "deploy_success", THE DeployResult SHALL have non-null contract_address field
5. WHEN status is "deploy_failed", THE DeployResult SHALL have non-null error_code field
6. WHEN status is "deploy_failed", THE DeployResult SHALL have non-null error_message field
7. THE tx_hash field SHALL be a valid transaction hash (0x + 64 hex characters)
8. THE contract_address field SHALL be a valid EVM address (0x + 40 hex characters)
