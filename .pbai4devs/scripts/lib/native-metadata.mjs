import { spawnSync } from 'node:child_process';
import { closeSync, existsSync, fchmodSync, fstatSync, ftruncateSync, lstatSync, openSync, readFileSync, renameSync, writeSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { win32 } from 'node:path';
import { linuxPythonXattrs } from './xattr-probe.mjs';

const nativePlatform = process.platform;
export const nativeMetadataCopySupported = nativePlatform === 'darwin' || nativePlatform === 'linux';

function windowsMetadataError(code, path, message = 'Cannot verify or preserve Windows file protections.') {
  return Object.assign(new Error(message), {
    code, ...(path === undefined ? {} : { path, metadataTargetVerified: true }),
  });
}

// Moving a file from the private stage onto an NTFS destination can change
// inherited ACEs. Update the original unnamed stream under an exclusive
// handle. Leave the stage available if the write or its verification fails.
const windowsUpdate = String.raw`
$ErrorActionPreference = 'Stop'
try {
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class AgentDevKitWindowsUpdate {
    [StructLayout(LayoutKind.Sequential)]
    private struct FileInformation {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Creation, LastAccess, LastWrite;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }

    [DllImport("kernel32.dll", ExactSpelling=true, CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security,
        uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);

    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)]
    private static extern bool SetFileInformationByHandle(SafeFileHandle file, int kind, ref byte value, uint size);

    public static FileStream Open(string path, bool writable, uint expectedVolume, ulong expectedIndex) {
        const uint genericRead = 0x80000000, genericWrite = 0x40000000, delete = 0x00010000;
        const uint openExisting = 3, openReparsePoint = 0x00200000;
        const uint readOnly = 0x1, directory = 0x10, reparsePoint = 0x400;
        var handle = CreateFileW(path, genericRead | (writable ? genericWrite : delete), 0,
            IntPtr.Zero, openExisting, openReparsePoint, IntPtr.Zero);
        try {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            FileInformation info;
            if (!GetFileInformationByHandle(handle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (info.Links != 1 || (info.Attributes & (readOnly | directory | reparsePoint)) != 0)
                throw new UnauthorizedAccessException();
            ulong index = ((ulong)info.IndexHigh << 32) | info.IndexLow;
            if (info.Volume != expectedVolume || index != expectedIndex) {
                handle.Dispose();
                return null;
            }
            return new FileStream(handle, writable ? FileAccess.ReadWrite : FileAccess.Read);
        } catch { handle.Dispose(); throw; }
    }

    public static void DiscardStage(FileStream stream) {
        const int fileDispositionInfo = 4;
        byte deleteOnClose = 1;
        if (!SetFileInformationByHandle(stream.SafeFileHandle, fileDispositionInfo, ref deleteOnClose, 1))
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }
}
'@
  $source = $null
  $target = $null
  $hash = [Security.Cryptography.SHA256]::Create()
  try {
    $source = [AgentDevKitWindowsUpdate]::Open($env:AGENTDEVKIT_REPLACEMENT, $false,
      [uint32]$env:AGENTDEVKIT_STAGE_VOLUME, [uint64]$env:AGENTDEVKIT_STAGE_INDEX)
    if ($null -eq $source) { exit 4 }
    $target = [AgentDevKitWindowsUpdate]::Open($env:AGENTDEVKIT_DESTINATION, $true,
      [uint32]$env:AGENTDEVKIT_DESTINATION_VOLUME, [uint64]$env:AGENTDEVKIT_DESTINATION_INDEX)
    if ($null -eq $target) { exit 4 }
    $before = [BitConverter]::ToString($hash.ComputeHash($target)).Replace('-', '')
    $planned = [BitConverter]::ToString($hash.ComputeHash($source)).Replace('-', '')
    if ($before -ine $env:AGENTDEVKIT_EXPECTED_BEFORE -or $planned -ine $env:AGENTDEVKIT_EXPECTED_AFTER) { exit 4 }
    $sections = [Security.AccessControl.AccessControlSections]'Access, Owner, Group'
    $acl = $target.GetAccessControl().GetSecurityDescriptorSddlForm($sections)
    $source.Position = 0
    $target.Position = 0
    $source.CopyTo($target)
    $target.SetLength($target.Position)
    $target.Flush($true)
    $target.Position = 0
    $updated = [BitConverter]::ToString($hash.ComputeHash($target)).Replace('-', '')
    if ($updated -ine $planned -or $target.GetAccessControl().GetSecurityDescriptorSddlForm($sections) -cne $acl) { exit 3 }
    [AgentDevKitWindowsUpdate]::DiscardStage($source)
  } finally {
    if ($null -ne $target) { $target.Dispose() }
    if ($null -ne $source) { $source.Dispose() }
    $hash.Dispose()
  }
} catch {
  $cause = $_.Exception.GetBaseException()
  if ($cause -is [UnauthorizedAccessException] -or
      ($cause -is [ComponentModel.Win32Exception] -and $cause.NativeErrorCode -in @(5, 32, 33))) { exit 2 }
  exit 3
}
`;

function windowsPowerShell(displayPath) {
  const root = process.env.SystemRoot;
  if (!root || !win32.isAbsolute(root)) throw windowsMetadataError('E_METADATA_UNSUPPORTED', displayPath);
  const executable = win32.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  if (!existsSync(executable)) throw windowsMetadataError('E_METADATA_UNSUPPORTED', displayPath);
  return executable;
}

export function checkWindowsFileChanges(entries) {
  if (nativePlatform !== 'win32' || !entries.length) return;
  const singleEntry = entries.length === 1 ? entries[0] : undefined;
  const executable = windowsPowerShell(singleEntry?.displayPath);
  // W_OK only checks the read-only attribute on Windows. Opening without
  // truncation or DeleteOnClose asks the OS to evaluate the effective rights
  // required by this plan. All paths are checked before any file is replaced.
  const probe = `
$ErrorActionPreference = 'Stop'
$index = 0
try {
  $reader = [IO.StreamReader]::new([Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false, $true))
  try { $request = ConvertFrom-Json -InputObject ($reader.ReadToEnd()) } finally { $reader.Dispose() }
  foreach ($entry in $request.entries) {
    $attrs = [IO.File]::GetAttributes($entry.path)
    $unsupported = [IO.FileAttributes]'ReadOnly, ReparsePoint'
    if ($attrs -band $unsupported) { [Console]::WriteLine($index); exit 2 }
    if (!$entry.update) {
      # The recovery snapshot stores only the unnamed data stream.
      $streams = @(Get-Item -LiteralPath $entry.path -Stream '*' -Force)
      if ($streams.Count -ne 1 -or $streams[0].Stream -cne ':$DATA') { [Console]::WriteLine($index); exit 2 }
    }
    $rights = [Security.AccessControl.FileSystemRights]'Read, Delete, Synchronize'
    $share = [IO.FileShare]'ReadWrite, Delete'
    if ($entry.update) {
      $rights = [Security.AccessControl.FileSystemRights]'Read, Write, Synchronize'
      $share = [IO.FileShare]::None
    }
    $handle = [IO.FileStream]::new($entry.path, [IO.FileMode]::Open,
      $rights, $share, 4096, [IO.FileOptions]::None)
    $handle.Dispose()
    $index++
  }
} catch {
  [Console]::WriteLine($index)
  $cause = $_.Exception.GetBaseException()
  if ($cause -is [UnauthorizedAccessException] -or
      ($cause -is [IO.IOException] -and ($cause.HResult -band 65535) -in @(32, 33))) { exit 2 }
  exit 3
}
`;
  verifyWindowsFileChangeProbe(entries, () => spawnSync(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', probe], {
    env: { SystemRoot: process.env.SystemRoot }, input: JSON.stringify({ entries: entries.map(({ path, update }) => ({ path, update })) }),
    encoding: 'utf8', windowsHide: true, timeout: 60000,
  }));
}

export function verifyWindowsFileChangeProbe(entries, run) {
  let result = run();
  // A timed-out preflight can be repeated because the probe only opens files
  // and closes the handles. Explicit denials and other errors are final.
  if (result.error?.code === 'ETIMEDOUT') result = run();
  if (result.error || result.status !== 0) {
    const singleEntry = entries.length === 1 ? entries[0] : undefined;
    const index = /^\d+$/.test(result.stdout?.trim()) ? Number(result.stdout.trim()) : null;
    const entry = index === null ? singleEntry : entries[index];
    if (!result.error && result.status === 2) throw windowsMetadataError('E_PROTECTED_METADATA', entry?.displayPath);
    let message = 'Windows file protection verification returned an unexpected result.';
    if (result.error) {
      message = result.error.code === 'ETIMEDOUT' ? 'Windows file protection verification timed out.'
        : 'Windows file protection verification could not run.';
    }
    throw windowsMetadataError('E_METADATA_UNSUPPORTED', entry?.displayPath, message);
  }
}

export function publishFileReplacement(staged, destination, displayPath, hashes) {
  if (nativePlatform !== 'win32') return renameSync(staged, destination);
  const executable = windowsPowerShell(displayPath);
  const sourceIdentity = lstatSync(staged, { bigint: true });
  const destinationIdentity = lstatSync(destination, { bigint: true });
  if (!sourceIdentity.ino || !destinationIdentity.ino) throw windowsMetadataError('E_METADATA_UNSUPPORTED', displayPath);
  const command = Buffer.from(windowsUpdate, 'utf16le').toString('base64');
  const temporary = tmpdir();
  const result = spawnSync(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', command], {
    env: {
      SystemRoot: process.env.SystemRoot, AGENTDEVKIT_REPLACEMENT: staged, AGENTDEVKIT_DESTINATION: destination,
      TEMP: temporary, TMP: temporary,
      AGENTDEVKIT_STAGE_VOLUME: BigInt.asUintN(32, sourceIdentity.dev).toString(),
      AGENTDEVKIT_STAGE_INDEX: BigInt.asUintN(64, sourceIdentity.ino).toString(),
      AGENTDEVKIT_DESTINATION_VOLUME: BigInt.asUintN(32, destinationIdentity.dev).toString(),
      AGENTDEVKIT_DESTINATION_INDEX: BigInt.asUintN(64, destinationIdentity.ino).toString(),
      AGENTDEVKIT_EXPECTED_BEFORE: hashes.before, AGENTDEVKIT_EXPECTED_AFTER: hashes.after,
    },
    windowsHide: true, stdio: 'ignore', timeout: 60000,
  });
  if (result.error || result.status !== 0) {
    const code = { 2: 'E_PROTECTED_METADATA', 4: 'E_CHANGED' }[result.status] || 'E_METADATA_UNSUPPORTED';
    throw windowsMetadataError(code, displayPath);
  }
}

const failure = cause => Object.assign(new Error('Native file metadata could not be preserved.'), {
  code: 'E_METADATA_COPY',
  ...(cause ? { cause } : {}),
});

function copyArgs(source, destination) {
  return nativePlatform === 'darwin'
    ? ['-p', source, destination]
    : ['--preserve=mode,ownership,timestamps,xattr', '--', source, destination];
}

function metadataOnlyArgs(source, destination) {
  return ['--attributes-only', '--preserve=mode,ownership,timestamps,xattr', '--', source, destination];
}

function runCopy(source, destination) {
  const result = spawnSync('/bin/cp', copyArgs(source, destination), {
    encoding: 'utf8', windowsHide: true, timeout: 30000,
    env: { ...process.env, LC_ALL: 'C' },
  });
  if (result.error || result.status !== 0 || nativePlatform === 'linux' && result.stderr.trim()) {
    throw failure(result.error || new Error('copy command reported a metadata warning'));
  }
}

function runMetadataCopy(source, destination) {
  const result = spawnSync('/bin/cp', metadataOnlyArgs(source, destination), {
    encoding: 'utf8', windowsHide: true, timeout: 30000,
    env: { ...process.env, LC_ALL: 'C' },
  });
  if (result.error || result.status !== 0 || result.stderr.trim()) {
    throw failure(result.error || new Error('metadata-only copy command failed'));
  }
}

function file(path) {
  const info = lstatSync(path, { bigint: true });
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1n) throw failure();
  return info;
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.isFile() && right.isFile()
    && !left.isSymbolicLink() && !right.isSymbolicLink();
}

function macFlags(path) {
  const result = spawnSync('/usr/bin/stat', ['-f', '%Sf', path], { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  if (result.error || result.status !== 0 || result.stderr.trim()) throw failure(result.error || new Error('file flags could not be read'));
  return result.stdout.trim() === '-' ? '' : result.stdout.trim();
}

function macMetadata(path) {
  const names = spawnSync('/usr/bin/xattr', [path], { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  if (names.error || names.status !== 0) throw failure(names.error || new Error('xattr list failed'));
  const attrs = [];
  for (const name of names.stdout.split(/\r?\n/).filter(Boolean).sort()) {
    const value = spawnSync('/usr/bin/xattr', ['-p', name, path], { encoding: 'buffer', windowsHide: true, timeout: 10000 });
    if (value.error || value.status !== 0 || !Buffer.isBuffer(value.stdout)) throw failure(value.error || new Error('xattr read failed'));
    attrs.push([name, value.stdout.toString('base64')]);
  }
  const acl = spawnSync('/bin/ls', ['-ldeO', path], {
    encoding: 'utf8', windowsHide: true, timeout: 10000,
    env: { ...process.env, LC_ALL: 'C' },
  });
  if (acl.error || acl.status !== 0) throw failure(acl.error || new Error('ACL read failed'));
  const entries = acl.stdout.split(/\r?\n/).filter(line => /^\s*\d+:\s/.test(line));
  return { attrs, entries, flags: macFlags(path) };
}

function linuxMetadata(path) {
  const result = {};
  if (existsSync('/usr/bin/getfattr')) {
    const attrs = spawnSync('/usr/bin/getfattr', ['-h', '-d', '-m', '-', '--absolute-names', '-e', 'hex', '--', path], {
      encoding: 'utf8', windowsHide: true, timeout: 10000,
      env: { ...process.env, LC_ALL: 'C' },
    });
    if (attrs.error || attrs.status !== 0 || attrs.stderr.trim()) throw failure(attrs.error || new Error('xattr verification failed'));
    result.xattrs = attrs.stdout.split(/\r?\n/).filter(line => !line.startsWith('# file:')).sort();
  } else {
    try {
      const attrs = linuxPythonXattrs(path);
      if (attrs !== null) result.xattrs = attrs;
    } catch (error) { throw failure(error); }
  }
  if (existsSync('/usr/bin/getfacl')) {
    const acl = spawnSync('/usr/bin/getfacl', ['--absolute-names', '--numeric', '--omit-header', '--', path], {
      encoding: 'utf8', windowsHide: true, timeout: 10000,
      env: { ...process.env, LC_ALL: 'C' },
    });
    if (acl.error || acl.status !== 0 || acl.stderr.trim()) throw failure(acl.error || new Error('ACL verification failed'));
    result.acl = acl.stdout.split(/\r?\n/).filter(Boolean).sort();
  }
  return Object.keys(result).length ? result : null;
}

function nativeMetadata(path) {
  if (nativePlatform === 'darwin') return macMetadata(path);
  if (nativePlatform === 'linux') return linuxMetadata(path);
  return null;
}

function matchesNativeMetadata(expected, path) {
  if (expected === null) return true;
  const actual = nativeMetadata(path);
  const provenance = 'com.apple.provenance';
  // macOS can assign provenance to a new file even when its source has none.
  // Keep any provenance already present on the source subject to exact verification.
  if (nativePlatform === 'darwin' && !expected.attrs.some(([name]) => name === provenance)) {
    actual.attrs = actual.attrs.filter(([name]) => name !== provenance);
  }
  return JSON.stringify(expected) === JSON.stringify(actual);
}

export function inspectMacWriteProtection(path) {
  if (nativePlatform !== 'darwin') return 'unknown';
  const result = spawnSync('/bin/ls', ['-ldeO', path], {
    encoding: 'utf8', windowsHide: true, timeout: 10000,
    env: { ...process.env, LC_ALL: 'C' },
  });
  if (result.error || result.status !== 0) return 'unknown';
  const lines = result.stdout.split(/\r?\n/), columns = lines[0]?.trim().split(/\s+/) || [];
  let flags; try { flags = macFlags(path); } catch { return 'unknown'; }
  if (/(?:^|,)(?:uchg|schg|uappnd|sappnd)(?:,|$)/.test(flags)) return 'protected';
  const deniedWrites = /\bdeny\b[^\n]*\b(?:write|append|add_file|delete|delete_child|writeattr|writeextattr|writesecurity|chown)\b/i;
  return lines.slice(1).some(line => /^\s*\d+:\s/.test(line) && deniedWrites.test(line)) ? 'protected' : 'clear';
}

function copyAndVerify(source, destination, destinationIdentity, metadataOnly = false) {
  const original = file(source), target = file(destination);
  if (destinationIdentity && !sameFile(destinationIdentity, target)) throw failure();
  const originalMetadata = nativeMetadata(source);
  if (!metadataOnly) runCopy(source, destination);
  else if (nativePlatform === 'linux') runMetadataCopy(source, destination);
  else if (nativePlatform === 'darwin') runCopy(source, destination);
  const sourceAfter = file(source), destinationAfter = file(destination);
  if (!sameFile(original, sourceAfter) || !sameFile(target, destinationAfter)
    || original.mode !== sourceAfter.mode || original.uid !== destinationAfter.uid || original.gid !== destinationAfter.gid
    || original.size !== sourceAfter.size || original.size !== destinationAfter.size
    || (!metadataOnly || nativePlatform === 'linux') && original.mtimeNs !== destinationAfter.mtimeNs
    || original.ctimeNs !== sourceAfter.ctimeNs
    || (original.mode & 0o7777n) !== (destinationAfter.mode & 0o7777n)) throw failure();
  if (!matchesNativeMetadata(originalMetadata, destination)) throw failure();
  return { source: sourceAfter, destination: destinationAfter, metadata: originalMetadata };
}

export function copyFileWithNativeMetadata(source, destination, { destinationIdentity, expectedBytes, metadataOnly = false } = {}) {
  if (!nativeMetadataCopySupported) throw failure();
  copyAndVerify(source, destination, destinationIdentity, metadataOnly);
  if (!metadataOnly && expectedBytes !== undefined && !readFileSync(destination).equals(expectedBytes)) throw failure();
}

export function stageReplacementWithNativeMetadata(source, staged, content, mode, displayPath = source) {
  if (!nativeMetadataCopySupported || process.platform !== nativePlatform) return false;
  const sourceIdentity = lstatSync(source, { bigint: true });
  if (!sourceIdentity.isFile() || sourceIdentity.isSymbolicLink() || sourceIdentity.nlink !== 1n) {
    throw Object.assign(new Error('File identity prevents a safe update.'), { code: 'E_PROTECTED_METADATA', path: displayPath });
  }
  if (nativePlatform === 'darwin' && inspectMacWriteProtection(source) !== 'clear') {
    throw Object.assign(new Error('File metadata prevents a safe update.'), { code: 'E_PROTECTED_METADATA', path: displayPath });
  }
  const bytes = Buffer.isBuffer(content) ? content : Buffer.from(content);
  const sourceBefore = file(source), stagedBefore = file(staged);
  let fd, primaryError, closeError;
  try {
    fd = openSync(staged, 'r+');
    const opened = fstatSync(fd, { bigint: true });
    if (!sameFile(stagedBefore, opened)) throw failure();
    copyAndVerify(source, staged, opened);
    const copied = fstatSync(fd, { bigint: true });
    if (!sameFile(opened, copied)) throw failure();
    for (let offset = 0; offset < bytes.length;) {
      const count = writeSync(fd, bytes, offset, bytes.length - offset, offset);
      if (count <= 0) throw failure();
      offset += count;
    }
    ftruncateSync(fd, bytes.length);
    if (mode !== undefined) fchmodSync(fd, mode);
    const completed = fstatSync(fd, { bigint: true });
    if (!sameFile(opened, completed) || (completed.mode & 0o777n) !== BigInt(mode ?? Number(sourceBefore.mode & 0o777n))) throw failure();
  } catch (error) {
    primaryError = error?.code === 'E_METADATA_COPY' ? error : failure(error);
  } finally {
    if (fd !== undefined) try { closeSync(fd); } catch (error) { closeError = error; }
  }
  if (primaryError && closeError) {
    const combined = new AggregateError([primaryError, closeError], primaryError.message, { cause: primaryError });
    combined.code = primaryError.code || 'E_METADATA_COPY';
    throw combined;
  }
  if (primaryError) throw primaryError;
  if (closeError) throw failure(closeError);
  const sourceAfter = file(source), stagedAfter = file(staged);
  if (!sameFile(sourceBefore, sourceAfter) || sourceBefore.size !== sourceAfter.size
    || sourceBefore.mtimeNs !== sourceAfter.mtimeNs || sourceBefore.ctimeNs !== sourceAfter.ctimeNs
    || !stagedAfter.isFile() || !readFileSync(staged).equals(bytes)) throw failure();
  const sourceMetadata = nativeMetadata(source);
  if (!matchesNativeMetadata(sourceMetadata, staged)) throw failure();
  return true;
}
