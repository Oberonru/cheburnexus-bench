const ts = require('typescript');
const fs = require('fs');
const { makeTransformer } = require('./transformer');

const srcFile = process.argv[2];
const tag = process.argv[3] || srcFile;
const src = fs.readFileSync(srcFile, 'utf8');

const result = ts.transpileModule(src, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2019,
    module: ts.ModuleKind.CommonJS,
    experimentalDecorators: true,
    useDefineForClassFields: false,
  },
  transformers: {
    before: [makeTransformer(tag)],
  },
  fileName: srcFile,
});

fs.writeFileSync(process.argv[4], result.outputText);
console.log('wrote', process.argv[4]);
