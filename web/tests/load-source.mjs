import ts from 'typescript';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

// Exercise the real TypeScript modules with Node's test runner, using the
// compiler already required by the app. The temporary output is always removed.
export function loadSource(relativePath) {
  const output = mkdtempSync(path.join(tmpdir(), 'scram-web-test-'));
  try {
    const root = path.resolve('src');
    const program = ts.createProgram([path.join(root, relativePath)], {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      resolveJsonModule: true, esModuleInterop: true, skipLibCheck: true,
      rootDir: root, outDir: output
    });
    const result = program.emit();
    if (result.emitSkipped) throw new Error('Could not compile test source');
    return createRequire(import.meta.url)(path.join(output, relativePath.replace(/\.ts$/, '.js')));
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
}
