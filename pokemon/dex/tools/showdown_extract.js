// Transpiles the Showdown data/*.ts files we need into plain JSON.
// esbuild only strips TypeScript syntax (no type-checking, no bundling of
// imports), which is exactly what we want: these files are plain JS object
// literals wearing a TS type-annotation costume. Embedded battle-scripting
// functions (onModifyMove, onStart, ...) survive the transform but are
// silently dropped by JSON.stringify since we only want the static fields.
const fs = require('fs');
const path = require('path');
const esbuild = require('esbuild');

const OUT_DIR = process.argv[2] || '/tmp/showdown_json';
fs.mkdirSync(OUT_DIR, { recursive: true });

// [sourceFile, outputName, exportSymbol?] -- exportSymbol defaults to
// outputName; it's given explicitly for files that export under the same
// symbol name as another file already in this list (data/mods/gen2/
// learnsets.ts exports `Learnsets`, same as data/learnsets.ts, so it needs
// a distinct outputName ("Gen2Learnsets") while still requiring the
// original `Learnsets` symbol out of its transpiled module).
const FILES = [
  ['data/pokedex.ts', 'Pokedex'],
  ['data/moves.ts', 'Moves'],
  ['data/abilities.ts', 'Abilities'],
  ['data/items.ts', 'Items'],
  ['data/learnsets.ts', 'Learnsets'],
  ['data/typechart.ts', 'TypeChart'],
  ['data/natures.ts', 'Natures'],
  ['data/formats-data.ts', 'FormatsData'],
  // Gen 1/2 learnset history: Showdown's main learnsets.ts only carries
  // "3"+ generation codes (Gen 3 is where the modern per-move-code format
  // starts) -- Gen 1/2-origin moves' "1"/"2"-prefixed codes live ONLY here.
  // Without this file, build_generation_availability() would (and did, in
  // the first pass) compute e.g. Tackle/Thundershock/Scratch as "Gen 3+",
  // which is wrong and would make GEN_1/GEN_2 rulesets wrongly exclude
  // core Gen 1 moves. See ARCHITECTURE.md addendum / TRACK_B_HANDOFF.md.
  ['data/mods/gen2/learnsets.ts', 'Gen2Learnsets', 'Learnsets'],
];

for (const [file, exportName, exportSymbol] of FILES) {
  const src = fs.readFileSync(file, 'utf8');
  let jsCode;
  try {
    const result = esbuild.transformSync(src, {
      loader: 'ts',
      format: 'cjs',
      target: 'es2020',
      sourcefile: file,
    });
    jsCode = result.code;
  } catch (err) {
    console.error(`TRANSFORM FAILED ${file}: ${err.message}`);
    continue;
  }

  const tmpFile = path.join(OUT_DIR, `_tmp_${exportName}.js`);
  fs.writeFileSync(tmpFile, jsCode);

  let mod;
  try {
    delete require.cache[require.resolve(path.resolve(tmpFile))];
    mod = require(path.resolve(tmpFile));
  } catch (err) {
    console.error(`EVAL FAILED ${file}: ${err.stack}`);
    continue;
  }
  const data = mod[exportSymbol || exportName];
  if (!data) {
    console.error(`SKIP ${file}: export "${exportSymbol || exportName}" not found after transform (keys: ${Object.keys(mod)})`);
    continue;
  }
  const outFile = path.join(OUT_DIR, `${exportName}.json`);
  fs.writeFileSync(outFile, JSON.stringify(data));
  console.log(`OK  ${exportName.padEnd(12)} ${Object.keys(data).length} entries -> ${outFile}`);
  fs.unlinkSync(tmpFile);
}
