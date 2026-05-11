/* eslint-disable no-undef */

const webpack = require("webpack");
const devCerts = require("office-addin-dev-certs");
const CopyWebpackPlugin = require("copy-webpack-plugin");
const HtmlWebpackPlugin = require("html-webpack-plugin");

const urlDev = "https://localhost:3000/";
const urlProd = process.env.PROD_URL || "https://localhost:3000/";

async function getHttpsOptions() {
  const httpsOptions = await devCerts.getHttpsServerOptions();
  return { ca: httpsOptions.ca, key: httpsOptions.key, cert: httpsOptions.cert };
}

module.exports = async (env, options) => {
  const dev = options.mode === "development";

  const config = {
    devtool: "source-map",
    entry: {
      polyfill: ["core-js/stable", "regenerator-runtime/runtime"],
      taskpane: ["./src/taskpane/index.tsx", "./src/taskpane/taskpane.html"],
      commands: ["./src/commands/commands.ts", "./src/commands/commands.html"],
    },
    output: {
      clean: true,
    },
    resolve: {
      extensions: [".ts", ".tsx", ".html", ".js"],
    },
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          exclude: /node_modules/,
          use: { loader: "babel-loader" },
        },
        {
          test: /\.css$/,
          use: ["style-loader", "css-loader"],
        },
        {
          test: /\.html$/,
          exclude: /node_modules/,
          use: "html-loader",
        },
        {
          test: /\.(png|jpg|jpeg|gif|ico)$/,
          type: "asset/resource",
          generator: { filename: "assets/[name][ext][query]" },
        },
      ],
    },
    plugins: [
      new HtmlWebpackPlugin({
        filename: "taskpane.html",
        template: "./src/taskpane/taskpane.html",
        chunks: ["polyfill", "taskpane"],
      }),
      new HtmlWebpackPlugin({
        filename: "commands.html",
        template: "./src/commands/commands.html",
        chunks: ["polyfill", "commands"],
      }),
      new CopyWebpackPlugin({
        patterns: [
          { from: "assets/*", to: "assets/[name][ext][query]" },
          {
            from: "manifest*.xml",
            to: "[name][ext]",
            transform(content) {
              if (dev) return content;
              return content.toString().replace(new RegExp(urlDev, "g"), urlProd);
            },
          },
        ],
      }),
      new webpack.DefinePlugin({
        "process.env.BACKEND_URL": JSON.stringify(process.env.BACKEND_URL || "http://localhost:8000"),
        "process.env.BACKEND_API_KEY": JSON.stringify(process.env.BACKEND_API_KEY || ""),
      }),
      new webpack.ProvidePlugin({ process: "process/browser.js" }),
    ],
    devServer: {
      hot: true,
      headers: { "Access-Control-Allow-Origin": "*" },
      server: { type: "https", options: env.WEBPACK_BUILD || options.https !== undefined ? options.https : await getHttpsOptions() },
      port: process.env.npm_package_config_dev_server_port || 3000,
    },
  };

  return config;
};
