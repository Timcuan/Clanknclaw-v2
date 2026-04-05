# Implementation Plan: Clanker v4.0.0 SDK Integration

## Overview

This plan implements the migration from Clanker v3.1.0 contract-based deployment to v4.0.0 SDK-based deployment. The implementation uses a Node.js wrapper script approach, allowing the Python application to invoke the TypeScript SDK via subprocess while maintaining clean separation of concerns and error handling.

## Tasks

- [ ] 1. Create Node.js wrapper script for Clanker SDK v4.0.0
  - Create `scripts/clanker_deploy.js` file
  - Implement configuration reading from command-line argument
  - Initialize Clanker SDK with environment variables (DEPLOYER_SIGNER_PRIVATE_KEY, BASE_RPC_URL)
  - Execute deployment transaction using SDK
  - Output JSON to stdout on success with status, txHash, contractAddress
  - Output JSON to stderr on error with status, errorCode, errorMessage
  - Exit with code 0 on success, non-zero on failure
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10_

- [ ]* 1.1 Write unit tests for Node.js wrapper script
  - Test configuration parsing
  - Test error handling for missing environment variables
  - Test JSON output format
  - _Requirements: 6.1, 6.2, 6.5, 6.6_

- [ ] 2. Implement SDK availability check in ClankerDeployer
  - Add `_check_sdk_availability()` method to verify Node.js is installed
  - Check Node.js version using subprocess
  - Log warning if Clanker SDK not installed globally
  - Return boolean without raising exceptions
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ]* 2.1 Write unit tests for SDK availability check
  - Mock subprocess to simulate Node.js present/absent
  - Verify correct boolean return values
  - Verify no exceptions raised
  - _Requirements: 3.5_

- [ ] 3. Implement configuration validation in preflight method
  - Validate token_name is non-empty and max 50 characters
  - Validate token_symbol is non-empty, max 10 characters, and uppercase
  - Validate tokenAdmin is valid EVM address
  - Validate image is valid IPFS URI (ipfs://...)
  - Validate tax_bps is between 0 and 10000 inclusive
  - Raise ValueError with descriptive message on validation failure
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ]* 3.1 Write property test for configuration validation
  - **Property 4: Input Validation Enforcement**
  - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**
  - Generate random invalid DeployRequest objects
  - Assert preflight raises ValueError for all invalid inputs

- [ ] 4. Implement subprocess execution in _execute_with_sdk method
  - Write configuration to temporary JSON file
  - Spawn subprocess with command ["node", script_path, config_file_path]
  - Set subprocess timeout to 120 seconds
  - Capture stdout, stderr, and exit code
  - Kill process on timeout
  - Delete temporary configuration file in finally block
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [ ]* 4.1 Write unit tests for subprocess execution
  - Mock subprocess to test success case
  - Mock subprocess to test timeout case
  - Verify temporary file cleanup
  - _Requirements: 4.6_

- [ ] 5. Implement output parsing in parse_sdk_output function
  - Return deploy_failed for non-zero exit code
  - Parse JSON from stdout
  - Extract txHash and contractAddress for status "success"
  - Extract errorCode and errorMessage for status "error"
  - Return deploy_failed with parse_error for malformed JSON
  - Never raise exceptions (catch all errors)
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

- [ ]* 5.1 Write property test for output parsing robustness
  - **Property 12: Output Parsing Robustness**
  - **Validates: Requirements 5.7**
  - Generate random stdout, stderr, exit_code combinations
  - Assert parse_sdk_output never raises exceptions
  - Assert always returns valid DeployResult

- [ ]* 5.2 Write unit tests for output parsing
  - Test success JSON parsing
  - Test error JSON parsing
  - Test malformed JSON handling
  - Test empty stdout handling
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [ ] 6. Update deploy method to use SDK execution
  - Call _check_sdk_availability() and return error if unavailable
  - Call build_clanker_v4_config() to create configuration
  - Call preflight() for validation
  - Call _execute_with_sdk() to execute deployment
  - Catch all exceptions and return deploy_failed DeployResult
  - Log error details for debugging
  - Never raise exceptions to caller
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9_

- [ ]* 6.1 Write property test for deploy method exception safety
  - **Property 13: Deploy Method Exception Safety**
  - **Validates: Requirements 7.8**
  - Generate random DeployRequest objects (valid and invalid)
  - Mock various error conditions
  - Assert deploy method never raises exceptions

- [ ] 7. Checkpoint - Ensure all tests pass
  - Run all unit tests and property tests
  - Verify no regressions in existing functionality
  - Ask the user if questions arise

- [ ] 8. Implement security measures
  - Verify private key is read from environment variable only
  - Add checks to prevent logging private keys
  - Pass private key to Node.js script via environment variables
  - Validate all EVM addresses to prevent injection
  - Validate IPFS URIs for well-formed format
  - Use absolute path for Node.js script
  - Validate RPC URL uses HTTPS protocol
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

- [ ]* 8.1 Write unit tests for security measures
  - Test address validation
  - Test IPFS URI validation
  - Test HTTPS enforcement for RPC URL
  - Test absolute path conversion
  - _Requirements: 8.5, 8.6, 8.7, 8.8_

- [ ] 9. Update configuration building to match v4.0.0 SDK structure
  - Verify build_clanker_v4_config() includes all required fields
  - Set pool.pairedToken to WETH address on Base (0x4200000000000000000000000000000000000006)
  - Set rewards.recipients with single recipient at 10000 bps
  - Set vault to null
  - Set devBuy to null
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11_

- [ ]* 9.1 Write property test for configuration structure
  - **Property 1: Configuration Structure Completeness**
  - **Validates: Requirements 1.1, 1.2**
  - Generate random valid DeployRequest objects
  - Assert build_clanker_v4_config returns dict with all required keys

- [ ]* 9.2 Write property test for configuration field mapping
  - **Property 2: Configuration Field Mapping Preservation**
  - **Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.8**
  - Generate random valid DeployRequest objects
  - Assert field mappings are preserved correctly

- [ ]* 9.3 Write property test for configuration constant values
  - **Property 3: Configuration Constant Values**
  - **Validates: Requirements 1.7, 1.9, 1.10, 1.11**
  - Generate random valid DeployRequest objects
  - Assert constant values are set correctly

- [ ] 10. Update DeployResult structure
  - Verify DeployResult has status field with "deploy_success" or "deploy_failed"
  - Verify success results have non-null tx_hash and contract_address
  - Verify failed results have non-null error_code and error_message
  - Add validation for tx_hash format (0x + 64 hex chars)
  - Add validation for contract_address format (0x + 40 hex chars)
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_

- [ ]* 10.1 Write property test for DeployResult structure
  - **Property 21: DeployResult Type Consistency**
  - **Property 22: DeployResult Status Validity**
  - **Property 23: Success Result Completeness**
  - **Property 24: Failure Result Completeness**
  - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6**
  - Generate random deployment outcomes
  - Assert DeployResult structure is always valid

- [ ] 11. Update environment configuration
  - Add NODE_SCRIPT_PATH to config.py with default value
  - Update .env.example with new environment variables
  - Remove CLANKER_CONTRACT_ADDRESS (no longer needed)
  - Document required Node.js dependencies
  - _Requirements: 6.9, 6.10, 8.1_

- [ ] 12. Checkpoint - Integration testing preparation
  - Ensure all unit tests pass
  - Verify Node.js script can be executed manually
  - Verify environment variables are configured
  - Ask the user if questions arise

- [ ]* 13. Write integration tests for end-to-end deployment
  - Test deployment on Base Sepolia testnet
  - Test deployment with insufficient gas
  - Test deployment timeout scenario
  - Test end-to-end workflow from candidate to deployment
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 14. Update documentation
  - Update CLANKER_INTEGRATION.md with v4.0.0 SDK approach
  - Document Node.js installation requirements
  - Document npm package installation steps
  - Add troubleshooting section for common errors
  - Update deployment workflow documentation
  - _Requirements: 3.4, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [ ] 15. Final checkpoint - Complete system verification
  - Run all tests (unit, property, integration)
  - Verify no regressions in existing functionality
  - Test deployment on testnet
  - Ensure all tests pass, ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The Node.js wrapper script is the bridge between Python and TypeScript SDK
- Security is critical: private keys must never be logged or written to disk
