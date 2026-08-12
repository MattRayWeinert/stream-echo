#!/usr/bin/env perl
# Chrome Native Messaging host — toggles Caps Lock on macOS (no Python).
# Protocol: https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
use strict;
use warnings;

binmode(STDIN);
binmode(STDOUT);

sub read_exact {
    my ( $fh, $n ) = @_;
    my $buf = '';
    my $got = 0;
    while ( $got < $n ) {
        my $r = sysread( $fh, $buf, $n - $got, $got );
        return undef unless defined $r;
        last if $r == 0;
        $got += $r;
    }
    return ( $got == $n ) ? $buf : undef;
}

sub json_escape {
    my ($s) = @_;
    $s =~ s/\\/\\\\/g;
    $s =~ s/"/\\"/g;
    $s =~ s/\r/\\r/g;
    $s =~ s/\n/\\n/g;
    return '"' . $s . '"';
}

sub respond {
    my ( $ok, $err ) = @_;
    my $json =
        $ok
      ? '{"ok":true}'
      : ( '{"ok":false,"error":' . json_escape($err) . '}' );
    syswrite( STDOUT, pack( 'V', length($json) ) );
    syswrite( STDOUT, $json );
}

while (1) {
    my $hdr = read_exact( \*STDIN, 4 );
    last unless defined $hdr;
    last if length($hdr) < 4;

    my $len = unpack( 'V', $hdr );
    last if $len > 4_194_304;

    my $payload = read_exact( \*STDIN, $len );
    last unless defined $payload;

    my @cmd = ( 'osascript', '-e', 'tell application "System Events" to key code 57' );
    my $rc  = system(@cmd);
    if ( $rc == -1 ) {
        respond( 0, "failed to start osascript: $!" );
    }
    elsif ( $rc != 0 ) {
        respond( 0, 'osascript exit ' . ( $rc >> 8 ) );
    }
    else {
        respond( 1, '' );
    }
}
