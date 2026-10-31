param(
  [string]$DockerConfig = '',
  [string]$DockerHost = ''
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskImage = 'pq35-cam-python-checks:local'
$dockerOptions = @()
if ($DockerConfig) { $dockerOptions += @('--config', $DockerConfig) }
if ($DockerHost) { $dockerOptions += @('--host', $DockerHost) }

& docker @dockerOptions build -t $taskImage -f (Join-Path $PSScriptRoot 'Dockerfile.camera-deps') $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Camera test dependency image build failed' }

# Windows can materialize the original Git car.capnp symlink as link text.
# Bind its original target into the test container without changing the checkout.
$carSchema = Join-Path $taskRoot 'opendbc_repo/opendbc/car/car.capnp'
$testCommand = 'python -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider tools/tests/test_camera*.py tools/tests/test_cityguide*.py -q --tb=short'
& docker @dockerOptions run --rm `
  --mount "type=bind,source=$taskRoot,target=/cam,readonly" `
  --mount "type=bind,source=$carSchema,target=/cam/cereal/car.capnp,readonly" `
  -e PYTHONPATH=/cam/opendbc_repo -w /cam $taskImage sh -c $testCommand
if ($LASTEXITCODE -ne 0) { throw 'Camera offline checks failed' }
