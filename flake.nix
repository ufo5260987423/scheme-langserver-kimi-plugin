{
  description = "MCP bridge connecting Kimi Code CLI to scheme-langserver";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;

      # Pin the scheme-langserver release version we want to provide.
      # Prebuilt binaries are published on GitHub Releases; on Linux x86_64 we
      # download the static glibc binary directly so we are not blocked on
      # nixpkgs updates. Other platforms fall back to the nixpkgs package if
      # it is available.
      schemeLangserverVersion = "2.1.2";

      # Build the Python package for a given system
      mkPackage = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.python313Packages.buildPythonApplication {
          pname = "scheme-langserver-bridge";
          version = "0.1.0";
          pyproject = true;

          src = self;

          build-system = [ pkgs.python313Packages.hatchling ];

          propagatedBuildInputs = [
            pkgs.python313Packages.mcp
          ];

          nativeCheckInputs = [
            pkgs.python313Packages.pytest
            pkgs.python313Packages.pytest-asyncio
          ];

          # We don't have scheme-langserver in the check environment,
          # so skip integration tests that require it.
          pytestFlagsArray = [
            "-k"
            "not integration"
          ];

          meta = {
            description = "MCP bridge for scheme-langserver to assist Kimi with Scheme code";
            license = pkgs.lib.licenses.mit;
            mainProgram = "scheme-langserver-bridge";
          };
        };

      # scheme-langserver from a GitHub Release binary (Linux x86_64 glibc).
      # For other systems we fall back to nixpkgs.
      mkSchemeLangserver = { pkgs, system }:
        if system == "x86_64-linux" then
          pkgs.stdenvNoCC.mkDerivation {
            pname = "scheme-langserver";
            version = schemeLangserverVersion;

            src = pkgs.fetchurl {
              url = "https://github.com/ufo5260987423/scheme-langserver/releases/download/${schemeLangserverVersion}/scheme-langserver-x86_64-linux-glibc";
              # SRI hash obtained from the GitHub release asset digest.
              hash = "sha256-pheo6HU8wqmpGjpoChQiYzAIXzfW6gKzqgE69CCJv2Y=";
            };

            dontUnpack = true;

            installPhase = ''
              mkdir -p $out/bin
              cp $src $out/bin/scheme-langserver
              chmod +x $out/bin/scheme-langserver
            '';

            meta = {
              description = "scheme-langserver ${schemeLangserverVersion} from GitHub Releases";
              license = pkgs.lib.licenses.mit;
              platforms = [ "x86_64-linux" ];
              mainProgram = "scheme-langserver";
            };
          }
        else
          pkgs.scheme-langserver or null;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonEnv = pkgs.python313;
          schemeLangserver = mkSchemeLangserver { inherit pkgs system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.uv
            ] ++ pkgs.lib.optional (schemeLangserver != null) schemeLangserver;

            shellHook = ''
              echo "scheme-langserver-bridge dev shell"
              echo "Python: $(python3 --version)"
              echo "uv: $(uv --version)"
              if command -v scheme-langserver &>/dev/null; then
                echo "scheme-langserver: $(scheme-langserver --help 2>&1 | head -1)"
              else
                echo "scheme-langserver: not in PATH (set SCHEME_LANGSERVER_PATH to your binary)"
              fi
            '';
          };
        });

      packages = forAllSystems (system: {
        default = mkPackage system;
        scheme-langserver-bridge = mkPackage system;
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/scheme-langserver-bridge";
        };
        scheme-langserver-bridge = {
          type = "app";
          program = "${self.packages.${system}.scheme-langserver-bridge}/bin/scheme-langserver-bridge";
        };
      });
    };
}
