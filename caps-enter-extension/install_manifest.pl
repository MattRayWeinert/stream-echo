#!/usr/bin/env perl
# Writes Native Messaging host manifests (no Python).
# Usage: perl install_manifest.pl YOUR_EXTENSION_ID
use strict;
use warnings;
use File::Path qw(make_path);
use File::Basename qw(dirname);
use Cwd qw(abs_path);

my $HOST_NAME = 'com.twitch_mirror_caps.toggle';

my @BROWSER_DIRS = (
    "$ENV{HOME}/Library/Application Support/Google/Chrome/NativeMessagingHosts",
    "$ENV{HOME}/Library/Application Support/Google/Chrome Canary/NativeMessagingHosts",
    "$ENV{HOME}/Library/Application Support/Chromium/NativeMessagingHosts",
    "$ENV{HOME}/Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts",
    "$ENV{HOME}/Library/Application Support/Microsoft Edge/NativeMessagingHosts",
    "$ENV{HOME}/Library/Application Support/Arc/User Data/NativeMessagingHosts",
    "$ENV{HOME}/Library/Application Support/Vivaldi/NativeMessagingHosts",
);

sub json_escape_str {
    my ($s) = @_;
    $s =~ s/\\/\\\\/g;
    $s =~ s/"/\\"/g;
    return $s;
}

sub main {
    my $ext_id = shift @ARGV;
    $ext_id =~ s/\s+//g if defined $ext_id;
    if ( !$ext_id ) {
        die "Usage: perl install_manifest.pl YOUR_EXTENSION_ID\n"
          . "(chrome://extensions → Developer mode → ID)\n";
    }

    my $root = dirname( abs_path(__FILE__) );
    my $host = "$root/native-host/caps_toggle_host.pl";
    die "Missing host: $host\n" unless -f $host;
    chmod 0755, $host;

    my $path_esc = json_escape_str($host);
    my $manifest = <<"JSON";
{
  "name": "$HOST_NAME",
  "path": "$path_esc",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://${ext_id}/"]
}
JSON

    for my $dir (@BROWSER_DIRS) {
        eval {
            make_path($dir);
            my $out = "$dir/$HOST_NAME.json";
            open my $fh, '>', $out or die "open $out: $!";
            print {$fh} $manifest;
            close $fh;
            print "Wrote $out\n";
        };
        warn "Skip $dir: $@" if $@;
    }

    print "\nQuit browser fully (⌘Q), reopen, reload extension.\n";
    return 0;
}

exit main();
