const HtmlWebpackPlugin = require('html-webpack-plugin');
const CopyPlugin = require("copy-webpack-plugin");

module.exports = {
  mode: 'production',
  entry: './src/script/application.ts',
  output: {
    filename: 'main.js',
    path: __dirname + '/build',
  },
  resolve: {
    extensions: ['.ts', '.js'],
  },
  stats: {
    children: true,
  },
  performance: {
    maxAssetSize: 1000000, // bytes
  },
  module: {
    rules: [
      { test: /\.ts$/, use: 'ts-loader' },
    ],
  },
  plugins: [
    new HtmlWebpackPlugin(
      {
        template: 'src/html/index.html'
      }
    ),
    new CopyPlugin({
      patterns: [
        { from: 'assets', to: 'assets' }
      ],
    }),
  ],
};