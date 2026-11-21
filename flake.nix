{
  description = "Token Garden release validation tools";

  inputs.shaka.url = "https://flakehub.com/f/kolohelios/shaka/*.tar.gz";
  inputs.nixpkgs.follows = "shaka/nixpkgs";

  outputs = { nixpkgs, shaka, ... }:
    let
      systems = [ "aarch64-darwin" "aarch64-linux" "x86_64-linux" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [
              shaka.packages.${system}.default
              pkgs.just
              pkgs.jujutsu
              pkgs.uv
              pkgs.python312
              pkgs.typos
              pkgs.actionlint
              pkgs.taplo
            ];
            UV_PROJECT_ENVIRONMENT = ".release-venv";
            UV_PYTHON = "${pkgs.python312}/bin/python3.12";
            UV_PYTHON_DOWNLOADS = "never";
          };
        });
    };
}
