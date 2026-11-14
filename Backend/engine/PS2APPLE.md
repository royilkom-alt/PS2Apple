# Engine PS2Apple

Source ini berasal dari `exoteric33/ps2-native-recomp` pada commit `e9a48aae8e0537f3a91d8584b2d78c6056723435`, yang berbasis `ps2-web-recomp` oleh Samir Nuri. Lisensi MIT dan pemberitahuan komponen upstream ikut disertakan. [Source upstream](https://github.com/exoteric33/ps2-native-recomp/tree/e9a48aae8e0537f3a91d8584b2d78c6056723435)

PS2Apple menambahkan host iPhoneOS, mode AOT tanpa generator kode runtime, scanout Metal, pembacaan ISO melalui `SYSTEM.CNF`, dan mode probe desktop untuk menyimpan kode EE yang belum diterjemahkan. Builder mengompilasi direktori ini dari paket aplikasi. Source game yang diterjemahkan berasal dari ISO lokal.
