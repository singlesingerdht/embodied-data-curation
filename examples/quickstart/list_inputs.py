"""列出第一条 episode 录到的通道（名字、模态、行数、字段），走公开的读取接口，不猜含义。"""
import argparse
import edc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data')
    args = parser.parse_args()
    with edc.open_dataset(args.data) as dataset:
        episode = next(iter(dataset.iter_episodes()))
        print(f'Episode: {episode.id}')
        print('source\tmodality\trows\tfields')
        with episode.opened():
            for name, channel in sorted(episode.channels.items()):
                print(f'{name}\t{channel.modality}\t{channel.count}\t{", ".join(channel.fields)}')


if __name__ == '__main__':
    main()
